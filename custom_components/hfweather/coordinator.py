"""One polling owner per entry; current weather is independent of optional products."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import WeatherAPIError, WeatherAuthError, WeatherClient, WeatherRateLimit
from .const import (
    CONF_LOCATION_ENTITY,
    CONF_LOCATION_MODE,
    DEFAULT_INTERVAL,
    LOCATION_DEBOUNCE_SECONDS,
    LOCATION_DISTANCE_KM,
    OPTIONAL_MAX_AGE,
)
from .models import (
    coordinates,
    distance_km,
    parse_air,
    parse_current,
    parse_forecast,
    parse_suggestions,
    parse_warnings,
    state_coordinates,
)

_LOGGER = logging.getLogger(__name__)


class HfCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass, entry, client: WeatherClient) -> None:
        self.entry = entry
        self.client = client
        self._last_coordinates: tuple[float, float] | None = None
        self._last_attempt = 0.0
        self._blocked_until = 0.0
        self._location_timer = None
        self._location_unsub = None
        self._optional: dict[str, tuple[float, tuple, Any, str]] = {}
        self._request_slots = asyncio.Semaphore(2)
        self.refresh_count = 0
        super().__init__(
            hass,
            _LOGGER,
            name="hfweather",
            config_entry=entry,
            update_interval=timedelta(
                minutes=entry.options.get("interval", DEFAULT_INTERVAL)
            ),
        )

    def current_coordinates(self) -> tuple[float, float] | None:
        mode = self.entry.options.get(
            CONF_LOCATION_MODE, self.entry.data.get(CONF_LOCATION_MODE, "fixed")
        )
        if mode == "entity":
            entity = self.entry.options.get(
                CONF_LOCATION_ENTITY, self.entry.data.get(CONF_LOCATION_ENTITY, "")
            )
            return state_coordinates(self.hass.states.get(entity))
        if mode == "home":
            return coordinates((self.hass.config.longitude, self.hass.config.latitude))
        return coordinates(
            (self.entry.data.get("longitude"), self.entry.data.get("latitude"))
        )

    @callback
    def start_location_tracking(self) -> None:
        mode = self.entry.options.get(
            CONF_LOCATION_MODE, self.entry.data.get(CONF_LOCATION_MODE, "fixed")
        )
        entity = self.entry.options.get(
            CONF_LOCATION_ENTITY, self.entry.data.get(CONF_LOCATION_ENTITY)
        )
        if mode == "entity" and entity:
            self._location_unsub = async_track_state_change_event(
                self.hass, [entity], self._location_changed
            )
            self.entry.async_on_unload(self.stop_location_tracking)

    @callback
    def stop_location_tracking(self) -> None:
        if self._location_unsub:
            self._location_unsub()
            self._location_unsub = None
        if self._location_timer:
            self._location_timer()
            self._location_timer = None

    @callback
    def _location_changed(self, _event) -> None:
        position = self.current_coordinates()
        if position is None or self._location_timer is not None:
            return
        if (
            self._last_coordinates
            and distance_km(position, self._last_coordinates) < LOCATION_DISTANCE_KM
        ):
            return
        # Movement may refresh sooner than a 12h poll, but never more often than 10min.
        delay = max(
            LOCATION_DEBOUNCE_SECONDS,
            600 - (time.monotonic() - self._last_attempt),
            self._blocked_until - time.monotonic(),
        )
        self._location_timer = async_call_later(
            self.hass, delay, self._refresh_location
        )

    async def _refresh_location(self, _now) -> None:
        self._location_timer = None
        position = self.current_coordinates()
        if position and (
            self._last_coordinates is None
            or distance_km(position, self._last_coordinates) >= LOCATION_DISTANCE_KM
        ):
            await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        if time.monotonic() < self._blocked_until:
            raise UpdateFailed("QWeather rate-limit cooldown active")
        position = self.current_coordinates()
        if position is None:
            raise UpdateFailed(
                "Configured location is unavailable; refusing to use a different location"
            )
        self._last_attempt = time.monotonic()
        try:
            current = parse_current(await self.client.request("weather/now", *position))
        except WeatherAuthError:
            raise ConfigEntryAuthFailed("QWeather authentication failed") from None
        except WeatherRateLimit as exc:
            self._blocked_until = time.monotonic() + exc.retry_after
            raise UpdateFailed("QWeather request limit reached") from None
        except (WeatherAPIError, ValueError):
            raise UpdateFailed("Unable to retrieve valid current weather") from None
        # Never reuse optional data belonging to another city.
        if position != self._last_coordinates:
            self._optional.clear()
        self._last_coordinates = position
        status: dict[str, dict] = {
            "current": {"available": True, "last_success": dt_util.utcnow().isoformat()}
        }
        options = self.entry.options
        jobs = {
            "air": ("air/now", parse_air),
            "warning": (
                "warning/now",
                lambda p: parse_warnings(p, options.get("disaster_level", 1)),
            ),
            "daily": (
                f"weather/{options.get('dailysteps', 7)}d",
                lambda p: parse_forecast(
                    p, hourly=False, zone=self.hass.config.time_zone
                ),
            ),
            "hourly": (
                f"weather/{options.get('hourlysteps', 24)}h",
                lambda p: parse_forecast(
                    p, hourly=True, zone=self.hass.config.time_zone
                ),
            ),
        }
        if options.get("sugg", False):
            jobs["suggestions"] = ("indices/1d", parse_suggestions)

        async def fetch(group, endpoint, parser):
            try:
                async with self._request_slots:
                    if time.monotonic() < self._blocked_until:
                        raise WeatherRateLimit(
                            int(self._blocked_until - time.monotonic()) + 1
                        )
                    result = parser(await self.client.request(endpoint, *position))
                observed = dt_util.utcnow().isoformat()
                self._optional[group] = (time.monotonic(), position, result, observed)
                status[group] = {"available": True, "last_success": observed}
                return group, result
            except WeatherAuthError:
                # Optional-product permission errors must not disable working weather.
                reason = "authentication_or_product_access"
            except WeatherRateLimit as exc:
                self._blocked_until = max(
                    self._blocked_until, time.monotonic() + exc.retry_after
                )
                reason = "rate_limited"
            except (WeatherAPIError, ValueError):
                reason = "fetch_failed"
            cached = self._optional.get(group)
            valid = (
                cached is not None
                and cached[1] == position
                and time.monotonic() - cached[0] <= OPTIONAL_MAX_AGE
            )
            status[group] = {
                "available": False,
                "error": reason,
                "last_success": cached[3] if cached else None,
                "cached": valid,
            }
            # Retain recent values for diagnostics only; entity.available stays False.
            return group, cached[2] if valid else (
                [] if group in ("daily", "hourly") else {}
            )

        result = dict(
            await asyncio.gather(*(fetch(group, *spec) for group, spec in jobs.items()))
        )
        self.refresh_count += 1
        return {
            "current": current,
            **result,
            "status": status,
            "updated_at": status["current"]["last_success"],
            "refresh_count": self.refresh_count,
        }
