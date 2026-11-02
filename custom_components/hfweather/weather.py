"""HA weather entity backed by the same current observation as the sensors."""

from homeassistant.components.weather import WeatherEntity, WeatherEntityFeature
from homeassistant.const import (
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)

from .const import DOMAIN
from .entity import HfEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([HfweatherEntity(hass.data[DOMAIN][entry.entry_id])])


class HfweatherEntity(HfEntity, WeatherEntity):
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_visibility_unit = UnitOfLength.KILOMETERS
    _attr_native_precipitation_unit = UnitOfLength.MILLIMETERS
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY
    )

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = coordinator.entry.title
        self._attr_unique_id = coordinator.entry.unique_id

    @property
    def native_temperature(self):
        return self.values.get("temperature")

    @property
    def native_apparent_temperature(self):
        return self.values.get("feelsLike")

    @property
    def native_dew_point(self):
        return self.values.get("dew")

    @property
    def humidity(self):
        return self.values.get("humidity")

    @property
    def cloud_coverage(self):
        return self.values.get("cloud")

    @property
    def native_pressure(self):
        return self.values.get("pressure")

    @property
    def native_wind_speed(self):
        return self.values.get("windSpeed")

    @property
    def wind_bearing(self):
        return self.values.get("wind360")

    @property
    def native_visibility(self):
        return self.values.get("vis")

    @property
    def condition(self):
        return self.values.get("condition")

    async def _forecast(self, group):
        data = self.coordinator.data or {}
        if not self.coordinator.last_update_success or not data.get("status", {}).get(
            group, {}
        ).get("available"):
            return None
        return [dict(row) for row in data.get(group, [])]

    async def async_forecast_daily(self):
        return await self._forecast("daily")

    async def async_forecast_hourly(self):
        return await self._forecast("hourly")

    def _handle_coordinator_update(self):
        super()._handle_coordinator_update()
        self.hass.async_create_task(self.async_update_listeners({"daily", "hourly"}))
