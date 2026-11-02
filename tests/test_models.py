"""No HA or network required: exercise real parsers, not reimplemented fixtures."""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "hf_models", Path(__file__).parents[1] / "custom_components/hfweather/models.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ModelsTest(unittest.TestCase):
    def test_current_numeric_units_and_unknown_fields(self):
        value = m.parse_current(
            {
                "now": {
                    "temp": "-2.5",
                    "humidity": "77",
                    "dew": "-4",
                    "wind360": "270",
                    "windDir": "西风",
                    "precip": "0.2",
                    "icon": "150",
                    "obsTime": "2026-11-03T10:00+08:00",
                },
                "fxLink": "https://www.qweather.com/weather/changchun-101060101.html",
            }
        )
        self.assertEqual(value["temperature"], -2.5)
        self.assertEqual(value["wind360"], 270)
        self.assertEqual(value["windDir"], "西风")
        self.assertEqual(value["condition"], "clear-night")
        self.assertEqual(value["place"], "changchun")
        self.assertIsNone(value["vis"])

    def test_invalid_current_does_not_become_zero(self):
        for payload in (
            {},
            {"now": {}},
            {"now": {"temp": "NaN"}},
            {"now": {"temp": None}},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                m.parse_current(payload)

    def test_short_forecast_keeps_actual_source_time(self):
        payload = {
            "hourly": [
                {"fxTime": "2026-11-04T01:00+08:00", "temp": "1", "icon": "99999"},
                {"fxTime": "2026-11-04T02:00+08:00", "temp": "2", "icon": "100"},
            ]
        }
        result = m.parse_forecast(payload, hourly=True, zone="Asia/Shanghai")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["datetime"], "2026-11-04T01:00:00+08:00")
        self.assertNotIn("condition", result[0])
        self.assertEqual(result[1]["native_temperature"], 2)

    def test_daily_timezone_and_precipitation(self):
        result = m.parse_forecast(
            {
                "daily": [
                    {
                        "fxDate": "2026-11-04",
                        "tempMax": "3",
                        "tempMin": "-5",
                        "wind360Day": "180",
                        "windSpeedDay": "9",
                        "precip": "2",
                    }
                ]
            },
            hourly=False,
            zone="Asia/Shanghai",
        )[0]
        self.assertEqual(result["datetime"], "2026-11-04T00:00:00+08:00")
        self.assertEqual(result["native_templow"], -5)
        self.assertEqual(result["native_precipitation"], 2)

    def test_forecast_rejects_malformed_payload(self):
        for payload in ({"daily": None}, {"daily": [None]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                m.parse_forecast(payload, hourly=False, zone="UTC")

    def test_warning_unknown_severity_not_suppressed(self):
        warning = m.parse_warnings(
            {"warning": [{"severity": "NewLevel", "title": "test", "text": "test"}]}, 6
        )
        self.assertEqual(warning["warning_count"], 1)
        self.assertEqual(m.parse_warnings({"warning": []}, 1)["warning_count"], 0)
        with self.assertRaises(ValueError):
            m.parse_warnings({}, 1)

    def test_suggestions_comfortable_key(self):
        self.assertEqual(
            m.parse_suggestions(
                {"daily": [{"type": "8", "category": "舒适", "text": "test"}]}
            ),
            {"comfortable": ["舒适", "test"]},
        )

    def test_air_co_stays_in_provider_mg_not_ug(self):
        self.assertEqual(
            m.parse_air({"now": {"aqi": "12", "co": "0.5", "pm2p5": "10"}})["co"], 0.5
        )

    def test_coordinates_guard(self):
        for value in (
            (181, 0),
            (0, 91),
            (True, 0),
            (float("nan"), 0),
            ("bad", 0),
            None,
        ):
            with self.subTest(value=value):
                self.assertIsNone(m.coordinates(value))
        self.assertEqual(m.coordinates(("120", "45")), (120, 45))

    def test_tracker_and_geocoder_order(self):
        self.assertEqual(
            m.state_coordinates(
                SimpleNamespace(
                    state="not_home", attributes={"latitude": 45, "longitude": 120}
                )
            ),
            (120, 45),
        )
        self.assertEqual(
            m.state_coordinates(
                SimpleNamespace(state="地址", attributes={"location": [45, 120]})
            ),
            (120, 45),
        )
        self.assertIsNone(
            m.state_coordinates(
                SimpleNamespace(state="unavailable", attributes={"location": [45, 120]})
            )
        )

    def test_distance(self):
        self.assertEqual(m.distance_km((120, 45), (120, 45)), 0)
        self.assertGreater(m.distance_km((120, 45), (121, 45)), 70)

    def test_nonfinite_and_times(self):
        self.assertIsNone(m.number("inf"))
        self.assertIsNone(m.timestamp("invalid"))
        self.assertEqual(
            m.timestamp("2026-11-03T00:00:00Z"), "2026-11-03T00:00:00+00:00"
        )


if __name__ == "__main__":
    unittest.main()
