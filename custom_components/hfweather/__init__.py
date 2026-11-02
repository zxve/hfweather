"""Set up the independent hfweather integration with an explicit legacy bridge."""

from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import entity_sources

from .api import WeatherClient
from .const import CONF_API_HOST, CONF_LEGACY_ENTRY, DEFAULT_HOST, DOMAIN, PLATFORMS
from .coordinator import HfCoordinator
from .migration import LegacyMigration, legacy_entry


async def async_setup_entry(hass, entry):
    client = WeatherClient(
        async_get_clientsession(hass),
        entry.data["api_key"],
        entry.data.get(CONF_API_HOST, DEFAULT_HOST),
    )
    coordinator = HfCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    transaction = None
    old_id = entry.data.get(CONF_LEGACY_ENTRY)
    if old_id and hass.config_entries.async_get_entry(old_id):
        try:
            transaction = LegacyMigration(hass, legacy_entry(hass, old_id), entry)
        except ValueError:
            raise ConfigEntryError("Legacy source entry is not compatible") from None
        await transaction.prepare()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        if transaction:
            if any(
                row.disabled_by is None and row.entity_id not in entity_sources(hass)
                for row in transaction.entities
            ):
                raise ConfigEntryError(
                    "A migrated entity did not load; retaining the legacy configuration"
                )
            await transaction.finish()
    except Exception:
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if transaction:
            await transaction.rollback()
        raise
    if old_id:
        data = dict(entry.data)
        data.pop(CONF_LEGACY_ENTRY, None)
        hass.config_entries.async_update_entry(entry, data=data)
    coordinator.start_location_tracking()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator:
        coordinator.stop_location_tracking()
    return True


async def _async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
