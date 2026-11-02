"""Explicit legacy import using HA registry migration APIs, not storage-file edits."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, LEGACY_DOMAIN


def legacy_entry(hass, entry_id):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != LEGACY_DOMAIN or not entry.unique_id:
        raise ValueError("No matching legacy integration")
    # Reject the unrelated c1pher configuration schema.
    if not all(
        key in entry.data for key in ("api_key", "latitude", "longitude", "api_version")
    ):
        raise ValueError("Not a legacy zxve/hfweather configuration")
    return entry


class LegacyMigration:
    """Move only this entry's entities/devices; roll back partial registry changes."""

    def __init__(self, hass, old, new):
        self.hass, self.old, self.new = hass, old, new
        self.entities = []
        self.devices = []
        self.was_loaded = old.state == ConfigEntryState.LOADED
        self.unloaded = False

    async def prepare(self):
        registry = er.async_get(self.hass)
        devices = dr.async_get(self.hass)
        rows = list(er.async_entries_for_config_entry(registry, self.old.entry_id))
        for row in rows:
            if row.platform != LEGACY_DOMAIN:
                raise ConfigEntryError("Unexpected entity platform; migration stopped")
            conflict = registry.async_get_entity_id(row.domain, DOMAIN, row.unique_id)
            if conflict and conflict != row.entity_id:
                raise ConfigEntryError(
                    "Existing hfweather entity conflicts; migration stopped"
                )
        device_rows = list(
            dr.async_entries_for_config_entry(devices, self.old.entry_id)
        )
        for device in device_rows:
            identifiers = {
                (DOMAIN if domain == LEGACY_DOMAIN else domain, value)
                for domain, value in device.identifiers
            }
            conflicts = [
                candidate
                for candidate in dr.async_entries_for_config_entry(
                    devices, self.new.entry_id
                )
                if candidate.id != device.id and candidate.identifiers & identifiers
            ]
            if conflicts:
                raise ConfigEntryError(
                    "Existing hfweather device conflicts; migration stopped"
                )
        if self.was_loaded:
            if not await self.hass.config_entries.async_unload(self.old.entry_id):
                raise ConfigEntryError(
                    "Legacy integration could not unload; no registry changes made"
                )
            self.unloaded = True
        try:
            # Detach entities first. Moving device ownership while its entities
            # still belong to the old entry makes HA legitimately remove them.
            for row in rows:
                registry.async_update_entity_platform(
                    row.entity_id,
                    DOMAIN,
                    new_config_entry_id=self.new.entry_id,
                    new_device_id=None,
                )
                self.entities.append(row)
            for device in device_rows:
                identifiers = {
                    (DOMAIN if domain == LEGACY_DOMAIN else domain, value)
                    for domain, value in device.identifiers
                }
                devices.async_update_device(
                    device.id,
                    new_config_entry_id=self.new.entry_id,
                    new_identifiers=identifiers,
                )
                self.devices.append(device)
            for row in rows:
                registry.async_update_entity(row.entity_id, device_id=row.device_id)
        except Exception:
            await self.rollback()
            raise ConfigEntryError(
                "Unable to migrate legacy weather registry; changes rolled back"
            ) from None

    async def rollback(self):
        registry = er.async_get(self.hass)
        devices = dr.async_get(self.hass)
        for row in self.entities:
            registry.async_update_entity(row.entity_id, device_id=None)
        for device in reversed(self.devices):
            devices.async_update_device(
                device.id,
                new_config_entry_id=self.old.entry_id,
                new_identifiers=set(device.identifiers),
            )
        for row in reversed(self.entities):
            registry.async_update_entity_platform(
                row.entity_id,
                row.platform,
                new_config_entry_id=self.old.entry_id,
                new_device_id=row.device_id,
            )
        self.entities.clear()
        self.devices.clear()
        if self.unloaded:
            self.unloaded = False
            await self.hass.config_entries.async_setup(self.old.entry_id)

    async def finish(self):
        # Removing the now-empty old entry cannot remove migrated entities/devices.
        if not await self.hass.config_entries.async_remove(self.old.entry_id):
            raise ConfigEntryError("Legacy entry cleanup failed")
