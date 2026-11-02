"""Run with the actual target HA installed, no external network or production config."""

import tempfile
import unittest
from types import MappingProxyType, SimpleNamespace

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.hfweather.api import (
    WeatherAPIError,
    WeatherAuthError,
    WeatherClient,
    WeatherRateLimit,
    validate_host,
)
from custom_components.hfweather.config_flow import options_schema
from custom_components.hfweather.const import DOMAIN
from custom_components.hfweather.coordinator import HfCoordinator
from custom_components.hfweather.sensor import SENSORS, HfweatherSensor
from custom_components.hfweather.weather import HfweatherEntity


def make_entry(domain=DOMAIN, **overrides):
    fields = dict(
        version=1,
        minor_version=1,
        domain=domain,
        title="Test",
        source="user",
        data={
            "api_key": "test-secret",
            "latitude": 45,
            "longitude": 120,
            "api_version": "v7",
        },
        options={},
        unique_id="120_0-45_0",
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
    )
    fields.update(overrides)
    return ConfigEntry(**fields)


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload, self.status, self.headers = payload, status, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class ApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_header_credential_no_query_and_redirect_disabled(self):
        session = FakeSession(FakeResponse({"code": "200", "now": {}}))
        await WeatherClient(session, "test-secret").request("weather/now", 120, 45)
        args, kwargs = session.calls[0]
        self.assertNotIn("test-secret", args[0])
        self.assertNotIn("key", kwargs["params"])
        self.assertEqual(kwargs["headers"]["X-QW-Api-Key"], "test-secret")
        self.assertFalse(kwargs["allow_redirects"])

    async def test_auth_rejected(self):
        for response in (FakeResponse({}, 401), FakeResponse({"code": "401"})):
            with self.assertRaises(WeatherAuthError):
                await WeatherClient(FakeSession(response), "secret").request(
                    "weather/now", 120, 45
                )

    async def test_redirect_not_followed(self):
        with self.assertRaises(WeatherAPIError):
            await WeatherClient(FakeSession(FakeResponse({}, 302)), "secret").request(
                "weather/now", 120, 45
            )

    async def test_quota_and_bad_retry_after(self):
        for header in ("60", "not-a-number", "999999"):
            with self.assertRaises(WeatherRateLimit) as raised:
                await WeatherClient(
                    FakeSession(FakeResponse({}, 429, {"Retry-After": header})),
                    "secret",
                ).request("weather/now", 120, 45)
            self.assertLessEqual(raised.exception.retry_after, 3600)

    async def test_invalid_json_and_shape(self):
        for payload in (ValueError("secret"), [], {"code": "error-secret"}):
            with self.assertRaises(WeatherAPIError) as raised:
                await WeatherClient(
                    FakeSession(FakeResponse(payload)), "secret"
                ).request("weather/now", 120, 45)
            self.assertNotIn("secret", str(raised.exception))

    async def test_no_warning_not_failure(self):
        result = await WeatherClient(
            FakeSession(FakeResponse({"code": "204"})), "secret"
        ).request("warning/now", 120, 45)
        self.assertEqual(result["warning"], [])

    def test_bad_hosts(self):
        for host in (
            "http://evil.test",
            "evil.test",
            "devapi.qweather.com.evil.test",
            "user@devapi.qweather.com",
            "devapi.qweather.com:443",
        ):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_host(host)


class RuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hfweather-test-")
        self.hass = HomeAssistant(self.temp.name)
        self.hass.config.time_zone = "Asia/Shanghai"
        self.entry = make_entry()
        self.calls = []
        self.temp_value = "1"
        self.failure = None
        self.client = SimpleNamespace(request=self.request)
        self.coordinator = HfCoordinator(self.hass, self.entry, self.client)

    async def asyncTearDown(self):
        await self.coordinator.async_shutdown()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def request(self, endpoint, lon, lat):
        self.calls.append((endpoint, lon, lat))
        if self.failure and self.failure[0] == endpoint:
            raise self.failure[1]
        if endpoint == "weather/now":
            return {
                "now": {
                    "temp": self.temp_value,
                    "icon": "100",
                    "humidity": "80",
                    "obsTime": "2026-11-03T12:00+08:00",
                },
                "fxLink": "https://www.qweather.com/weather/test-123.html",
            }
        if endpoint == "air/now":
            return {"now": {"aqi": "12", "co": "0.5", "pm2p5": "10"}}
        if endpoint == "warning/now":
            return {"warning": []}
        if endpoint.endswith("d"):
            return {
                "daily": [
                    {
                        "fxDate": "2026-11-03",
                        "tempMax": "3",
                        "tempMin": "-2",
                        "iconDay": "100",
                    }
                ]
            }
        if endpoint.endswith("h"):
            return {
                "hourly": [
                    {"fxTime": "2026-11-03T13:00+08:00", "temp": "2", "icon": "100"}
                ]
            }
        if endpoint == "indices/1d":
            return {"daily": []}
        raise AssertionError("Unexpected endpoint")

    async def update(self):
        self.coordinator.async_set_updated_data(
            await self.coordinator._async_update_data()
        )

    async def test_two_updates_change_existing_sensor_and_weather(self):
        await self.update()
        sensor = HfweatherSensor(self.coordinator, SENSORS[0])
        weather = HfweatherEntity(self.coordinator)
        self.assertEqual(sensor.native_value, 1)
        self.temp_value = "9"
        await self.update()
        self.assertEqual(sensor.native_value, 9)
        self.assertEqual(weather.native_temperature, 9)
        self.assertEqual(len([c for c in self.calls if c[0] == "weather/now"]), 2)

    async def test_optional_air_failure_does_not_disable_weather(self):
        self.failure = ("air/now", WeatherAPIError("offline"))
        await self.update()
        self.assertTrue(HfweatherEntity(self.coordinator).available)
        pm = next(d for d in SENSORS if d.key == "pm25")
        self.assertFalse(HfweatherSensor(self.coordinator, pm).available)

    async def test_optional_cached_values_marked_unavailable(self):
        await self.update()
        self.failure = ("warning/now", WeatherAPIError("offline"))
        await self.update()
        state = self.coordinator.data["status"]["warning"]
        self.assertFalse(state["available"])
        self.assertTrue(state["cached"])
        warning = next(d for d in SENSORS if d.key == "disaster_warn")
        self.assertFalse(HfweatherSensor(self.coordinator, warning).available)

    async def test_auth_failure_requests_reauth(self):
        self.failure = ("weather/now", WeatherAuthError("invalid"))
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.coordinator._async_update_data()

    async def test_rate_limit_prevents_immediate_retry(self):
        self.failure = ("weather/now", WeatherRateLimit(120))
        with self.assertRaises(Exception):
            await self.coordinator._async_update_data()
        n = len(self.calls)
        with self.assertRaises(Exception):
            await self.coordinator._async_update_data()
        self.assertEqual(len(self.calls), n)

    async def test_forecast_time_and_failure(self):
        await self.update()
        weather = HfweatherEntity(self.coordinator)
        self.assertEqual(
            (await weather.async_forecast_hourly())[0]["datetime"],
            "2026-11-03T13:00:00+08:00",
        )
        self.failure = ("weather/24h", WeatherAPIError("offline"))
        await self.update()
        self.assertIsNone(await weather.async_forecast_hourly())
        self.assertTrue(weather.available)

    async def test_warning_state_length_limit(self):
        await self.update()
        self.coordinator.data["warning"]["disaster_warn"] = "预警" * 400
        warning = HfweatherSensor(
            self.coordinator, next(d for d in SENSORS if d.key == "disaster_warn")
        )
        self.assertEqual(len(warning.native_value), 255)
        self.assertEqual(len(warning.extra_state_attributes["warning_text"]), 800)

    async def test_units_and_stable_ids(self):
        await self.update()
        sensor = HfweatherSensor(
            self.coordinator, next(d for d in SENSORS if d.key == "dew")
        )
        self.assertEqual(sensor.native_unit_of_measurement, "°C")
        self.assertEqual(sensor.unique_id, "120_0-45_0-dew")
        self.assertEqual(
            HfweatherEntity(self.coordinator).unique_id, self.entry.unique_id
        )
        self.assertIsNone(
            next(d for d in SENSORS if d.key == "windDir").native_unit_of_measurement
        )

    async def test_instances_do_not_mutate_definitions(self):
        before = tuple(SENSORS)
        entry = make_entry(options={"sugg": True})
        other = HfCoordinator(self.hass, entry, self.client)
        await other._async_update_data()
        await other.async_shutdown()
        self.assertEqual(SENSORS, before)
        self.assertTrue(all(d.group != "suggestions" for d in SENSORS))

    async def test_tracker_mode_and_missing_location(self):
        entry = make_entry(
            options={
                "location_mode": "entity",
                "location_entity": "device_tracker.test",
            }
        )
        other = HfCoordinator(self.hass, entry, self.client)
        with self.assertRaises(Exception):
            await other._async_update_data()
        self.hass.states.async_set(
            "device_tracker.test", "not_home", {"latitude": 46, "longitude": 121}
        )
        await other._async_update_data()
        self.assertEqual(self.calls[-1][1:], (121, 46))
        await other.async_shutdown()

    async def test_entity_unavailable_on_failed_coordinator(self):
        await self.update()
        self.coordinator.last_update_success = False
        self.assertFalse(HfweatherSensor(self.coordinator, SENSORS[0]).available)
        self.assertFalse(HfweatherEntity(self.coordinator).available)

    async def test_options_validate_discrete_endpoint_values(self):
        schema = options_schema(self.entry)
        with self.assertRaises(Exception):
            schema({"dailysteps": 4, "hourlysteps": 25})

    async def test_location_timer_is_coalesced_and_cancelled(self):
        self.coordinator._location_changed(None)
        timer = self.coordinator._location_timer
        self.assertIsNotNone(timer)
        self.coordinator._location_changed(None)
        self.assertIs(self.coordinator._location_timer, timer)
        self.coordinator.stop_location_tracking()
        self.assertIsNone(self.coordinator._location_timer)

    async def test_diagnostics_exclude_credentials_and_position(self):
        from custom_components.hfweather.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        await self.update()
        self.hass.data[DOMAIN] = {self.entry.entry_id: self.coordinator}
        result = await async_get_config_entry_diagnostics(self.hass, self.entry)
        self.assertNotIn("test-secret", repr(result))
        self.assertNotIn("longitude", repr(result))
        self.assertNotIn("latitude", repr(result))

    async def test_entities_do_not_shadow_reserved_group_attribute(self):
        await self.update()
        for entity in (
            HfweatherSensor(self.coordinator, SENSORS[0]),
            HfweatherEntity(self.coordinator),
        ):
            self.assertIsNone(getattr(entity, "group", None))


if __name__ == "__main__":
    unittest.main()
