"""Pure parsing. Keep provider times, SI values, unknown conditions and short responses."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def coordinates(value: Any) -> tuple[float, float] | None:
    """Return longitude, latitude; reject bools, NaN and invalid coordinates."""
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 2
        or any(isinstance(v, bool) for v in value)
    ):
        return None
    lon, lat = (number(v) for v in value)
    return (
        (lon, lat)
        if lon is not None
        and lat is not None
        and -180 <= lon <= 180
        and -90 <= lat <= 90
        else None
    )


def state_coordinates(state: Any) -> tuple[float, float] | None:
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    attrs = state.attributes
    if "longitude" in attrs and "latitude" in attrs:
        return coordinates((attrs["longitude"], attrs["latitude"]))
    # Companion geocoded_location uses [latitude, longitude].
    location = attrs.get("location")
    if isinstance(location, (tuple, list)) and len(location) == 2:
        return coordinates((location[1], location[0]))
    return None


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    v = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(min(1.0, math.sqrt(v)))


def timestamp(value: Any, zone: str = "Asia/Shanghai") -> str | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(zone))
        return dt.isoformat()
    except (ValueError, KeyError):
        return None


def condition(icon: Any, text: str = "") -> str | None:
    try:
        code = int(icon)
    except (ValueError, TypeError):
        code = -1
    if code in (100, 150):
        return "clear-night" if code == 150 else "sunny"
    if code in (101, 102, 103, 151, 152, 153):
        return "partlycloudy"
    if code == 104:
        return "cloudy"
    if code in (302, 303):
        return "lightning-rainy"
    if code == 304:
        return "hail"
    if code in (307, 308, 310, 311, 312, 314, 315, 316, 318):
        return "pouring"
    if 300 <= code <= 399:
        return "rainy"
    if code in (404, 405, 406, 456):
        return "snowy-rainy"
    if 400 <= code <= 499:
        return "snowy"
    if code in (503, 504, 507, 508):
        return "exceptional"
    if 500 <= code <= 515:
        return "fog"
    return {
        "晴": "sunny",
        "多云": "partlycloudy",
        "阴": "cloudy",
        "小雨": "rainy",
        "雪": "snowy",
    }.get(text)


def parse_current(payload: dict) -> dict:
    now = payload.get("now")
    if not isinstance(now, dict) or number(now.get("temp")) is None:
        raise ValueError("Invalid current weather payload")
    values = {
        key: number(now.get(key))
        for key in (
            "humidity",
            "feelsLike",
            "precip",
            "windSpeed",
            "dew",
            "pressure",
            "vis",
            "cloud",
            "wind360",
        )
    }
    values.update(
        temperature=number(now.get("temp")),
        text=now.get("text"),
        windDir=now.get("windDir"),
        windScale=now.get("windScale"),
        updatetime=timestamp(now.get("obsTime")),
        condition=condition(now.get("icon"), now.get("text", "")),
    )
    # Preserve the old 'place' entity as a provider URL slug, not a geocoded address.
    link = payload.get("fxLink")
    if isinstance(link, str):
        slug = urlsplit(link).path.rsplit("/", 1)[-1].removesuffix(".html")
        values["place"] = slug.rsplit("-", 1)[0] if "-" in slug else slug or None
    return values


def parse_air(payload: dict) -> dict:
    now = payload.get("now")
    if not isinstance(now, dict) or number(now.get("aqi")) is None:
        raise ValueError("Invalid air quality payload")
    result = {key: number(now.get(key)) for key in ("pm10", "no2", "so2", "co", "o3")}
    result.update(
        pm25=number(now.get("pm2p5")),
        qlty=number(now.get("aqi")),
        category=now.get("category"),
        primary=now.get("primary"),
        level=now.get("level"),
    )
    return result


def parse_warnings(payload: dict, threshold: int) -> dict:
    warnings = payload.get("warning")
    if not isinstance(warnings, list) or any(not isinstance(w, dict) for w in warnings):
        raise ValueError("Invalid warning payload")
    levels = {
        "Cancel": 0,
        "None": 0,
        "Unknown": 0,
        "Standard": 1,
        "Minor": 2,
        "Moderate": 3,
        "Major": 4,
        "Severe": 5,
        "Extreme": 6,
    }
    selected = [
        w
        for w in warnings
        if w.get("severity") not in levels or levels[w["severity"]] >= threshold
    ]
    text = " || ".join(
        f"{w.get('title', '预警')}:{w.get('text', '')}" for w in selected
    )
    return {
        "disaster_warn": text or "当前无符合筛选级别的预警",
        "warning_count": len(selected),
    }


def parse_suggestions(payload: dict) -> dict:
    daily = payload.get("daily")
    if not isinstance(daily, list) or any(not isinstance(row, dict) for row in daily):
        raise ValueError("Invalid indices payload")
    mapping = {
        "1": "sport",
        "2": "cw",
        "3": "drsg",
        "5": "uv",
        "6": "trav",
        "7": "guomin",
        "8": "comfortable",
        "9": "flu",
        "10": "air",
        "11": "kongtiao",
        "12": "sunglass",
        "14": "liangshai",
        "15": "jiaotong",
        "16": "fangshai",
    }
    return {
        mapping[str(row.get("type"))]: [row.get("category"), row.get("text")]
        for row in daily
        if str(row.get("type")) in mapping
    }


def parse_forecast(payload: dict, *, hourly: bool, zone: str) -> list[dict]:
    rows = payload.get("hourly" if hourly else "daily")
    if not isinstance(rows, list):
        raise ValueError("Invalid forecast payload")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid forecast row")
        time = timestamp(row.get("fxTime" if hourly else "fxDate"), zone)
        if time is None:
            continue
        entry = {
            "datetime": time,
            "condition": condition(
                row.get("icon" if hourly else "iconDay"),
                row.get("text" if hourly else "textDay", ""),
            ),
            "native_temperature": number(row.get("temp" if hourly else "tempMax")),
            "native_wind_speed": number(
                row.get("windSpeed" if hourly else "windSpeedDay")
            ),
            "wind_bearing": number(row.get("wind360" if hourly else "wind360Day")),
            "humidity": number(row.get("humidity")),
            "native_precipitation": number(row.get("precip")),
            "precipitation_probability": number(row.get("pop")),
        }
        if not hourly:
            entry["native_templow"] = number(row.get("tempMin"))
        result.append({k: v for k, v in entry.items() if v is not None})
    return result
