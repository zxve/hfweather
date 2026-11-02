"""Asynchronous v7 QWeather client; never include credentials in URLs or logs."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any

import aiohttp

from .const import DEFAULT_HOST


class WeatherAPIError(Exception):
    """Credential-free error safe for HA logs."""


class WeatherAuthError(WeatherAPIError):
    """The provider rejected the credential."""


class WeatherRateLimit(WeatherAPIError):
    def __init__(self, retry_after: int = 300) -> None:
        super().__init__("QWeather request limit reached")
        self.retry_after = max(30, min(retry_after, 3600))


def validate_host(value: str) -> str:
    """Only send a key to a QWeather HTTPS origin; never follow redirects."""
    host = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
        raise ValueError("Enter a QWeather hostname without a URL or port")
    if not (host.endswith(".qweather.com") or host.endswith(".qweatherapi.com")):
        raise ValueError("API host must belong to QWeather")
    return host


class WeatherClient:
    def __init__(
        self, session: aiohttp.ClientSession, api_key: str, host: str = DEFAULT_HOST
    ) -> None:
        self.session = session  # Owned by HA, never closed here.
        self._api_key = api_key
        self.host = validate_host(host)

    async def request(
        self, endpoint: str, longitude: float, latitude: float
    ) -> dict[str, Any]:
        if not re.fullmatch(
            r"(?:weather/(?:now|3d|7d|10d|15d|30d|24h|72h|168h)|air/now|warning/now|indices/1d)",
            endpoint,
        ):
            raise ValueError("Unsupported v7 endpoint")
        params = {
            "location": f"{longitude:.2f},{latitude:.2f}",
            "lang": "zh",
            "unit": "m",
        }
        if endpoint.startswith("indices/"):
            params["type"] = "0"
        try:
            async with self.session.get(
                f"https://{self.host}/v7/{endpoint}",
                params=params,
                headers={"X-QW-Api-Key": self._api_key},
                timeout=aiohttp.ClientTimeout(total=12),
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    raise WeatherAuthError("QWeather authentication failed")
                if response.status == 429:
                    try:
                        delay = int(response.headers.get("Retry-After", "300"))
                    except ValueError:
                        delay = 300
                    raise WeatherRateLimit(delay)
                if response.status != 200:
                    raise WeatherAPIError(f"QWeather HTTP {response.status}")
                try:
                    payload = await response.json()
                except (ValueError, aiohttp.ContentTypeError):
                    raise WeatherAPIError("QWeather returned invalid JSON") from None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise WeatherAPIError("QWeather connection failed or timed out") from None
        if not isinstance(payload, Mapping):
            raise WeatherAPIError("QWeather returned an invalid response")
        code = str(payload.get("code", ""))
        if code == "401":
            raise WeatherAuthError("QWeather authentication failed")
        if code == "429":
            raise WeatherRateLimit()
        if code == "204" and endpoint == "warning/now":
            return {"code": "200", "warning": []}
        if code != "200":
            raise WeatherAPIError("QWeather API returned an unsuccessful status")
        return dict(payload)
