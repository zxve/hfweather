"""Useful diagnostics without keys, coordinates, warnings or personal entity IDs."""

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data = coordinator.data if coordinator and coordinator.data else {}
    return {
        "integration": DOMAIN,
        "options": {
            k: v
            for k, v in entry.options.items()
            if k not in ("location_entity", "location")
        },
        "last_update_success": coordinator.last_update_success
        if coordinator
        else False,
        "refresh_count": data.get("refresh_count"),
        "status": data.get("status", {}),
        "observation_time": data.get("current", {}).get("updatetime"),
    }
