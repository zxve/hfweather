"""Shared identity and fresh-data access for all entities."""

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN


class HfEntity(CoordinatorEntity):
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator, group="current"):
        super().__init__(coordinator)
        self._product_group = group
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.unique_id)},
            name=coordinator.entry.title,
            manufacturer="QWeather",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def values(self):
        return (self.coordinator.data or {}).get(self._product_group, {})

    @property
    def available(self):
        return super().available and bool(
            (self.coordinator.data or {})
            .get("status", {})
            .get(self._product_group, {})
            .get("available")
        )

    @property
    def extra_state_attributes(self):
        state = (
            (self.coordinator.data or {}).get("status", {}).get(self._product_group, {})
        )
        result = {"last_successful_update": state.get("last_success")}
        if self._product_group == "current":
            result["observation_time"] = self.values.get("updatetime")
        return result
