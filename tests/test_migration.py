"""Migration tests against actual HA registries, kept in a disposable config dir."""

import tempfile
import unittest
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from test_runtime import make_entry

from custom_components.hfweather.config_flow import ConfigFlow
from custom_components.hfweather.migration import LegacyMigration


class MigrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hfweather-migration-")
        self.hass = HomeAssistant(self.temp.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.old = make_entry("heweather")
        self.new = make_entry("hfweather")
        self.hass.config_entries._entries[self.old.entry_id] = self.old
        self.hass.config_entries._entries[self.new.entry_id] = self.new
        dr.async_setup(self.hass)
        await dr.async_load(self.hass)
        await er.async_load(self.hass)
        self.registry = er.async_get(self.hass)
        self.devices = dr.async_get(self.hass)
        self.device = self.devices.async_get_or_create(
            config_entry_id=self.old.entry_id,
            identifiers={("heweather", "120_0-45_0")},
            name="Weather",
        )
        self.sensor = self.registry.async_get_or_create(
            "sensor",
            "heweather",
            "120_0-45_0-dew",
            suggested_object_id="hfweather_dew",
            config_entry=self.old,
            device_id=self.device.id,
        )
        self.weather = self.registry.async_get_or_create(
            "weather",
            "heweather",
            "120_0-45_0",
            suggested_object_id="hfweather",
            config_entry=self.old,
            device_id=self.device.id,
        )

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_migrate_preserves_ids_and_device(self):
        migration = LegacyMigration(self.hass, self.old, self.new)
        await migration.prepare()
        row = self.registry.async_get(self.sensor.entity_id)
        self.assertEqual(row.platform, "hfweather")
        self.assertEqual(row.config_entry_id, self.new.entry_id)
        self.assertEqual(row.unique_id, self.sensor.unique_id)
        self.assertEqual(row.entity_id, self.sensor.entity_id)
        self.assertEqual(row.device_id, self.device.id)
        self.assertEqual(
            self.devices.async_get(self.device.id).identifiers,
            {("hfweather", "120_0-45_0")},
        )
        self.assertEqual(
            self.registry.async_get(self.weather.entity_id).entity_id,
            self.weather.entity_id,
        )

    async def test_rollback_preserves_original_registry(self):
        migration = LegacyMigration(self.hass, self.old, self.new)
        await migration.prepare()
        await migration.rollback()
        self.assertEqual(
            self.registry.async_get(self.sensor.entity_id).platform, "heweather"
        )
        self.assertEqual(
            self.registry.async_get(self.sensor.entity_id).config_entry_id,
            self.old.entry_id,
        )
        self.assertEqual(
            self.devices.async_get(self.device.id).identifiers,
            {("heweather", "120_0-45_0")},
        )

    async def test_conflict_aborts_without_changes(self):
        self.registry.async_get_or_create(
            "sensor", "hfweather", self.sensor.unique_id, config_entry=self.new
        )
        migration = LegacyMigration(self.hass, self.old, self.new)
        with self.assertRaises(ConfigEntryError):
            await migration.prepare()
        self.assertEqual(
            self.registry.async_get(self.sensor.entity_id).platform, "heweather"
        )

    async def test_partial_failure_rolls_back(self):
        original = self.registry.async_update_entity_platform
        count = 0

        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError("simulated failure")
            return original(*args, **kwargs)

        migration = LegacyMigration(self.hass, self.old, self.new)
        with patch.object(
            type(self.registry), "async_update_entity_platform", side_effect=fail_second
        ):
            with self.assertRaises(ConfigEntryError):
                await migration.prepare()
        for row in (self.sensor, self.weather):
            self.assertEqual(
                self.registry.async_get(row.entity_id).platform, "heweather"
            )
        self.assertEqual(
            self.devices.async_get(self.device.id).identifiers,
            {("heweather", "120_0-45_0")},
        )

    async def test_import_copies_configuration_without_new_entity_identity(self):
        self.hass.config_entries._entries.pop(self.new.entry_id)
        flow = ConfigFlow()
        flow.hass = self.hass
        flow.context = {"source": "import"}
        flow.flow_id = "test-import"
        flow.handler = "hfweather"
        result = await flow.async_step_import({"legacy_entry_id": self.old.entry_id})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["api_key"], self.old.data["api_key"])
        self.assertEqual(result["options"]["location_mode"], "fixed")
        self.assertEqual(flow.unique_id, self.old.unique_id)

    async def test_import_rejects_unrelated_entry(self):
        flow = ConfigFlow()
        flow.hass = self.hass
        result = await flow.async_step_import({"legacy_entry_id": self.new.entry_id})
        self.assertEqual(result["reason"], "invalid_legacy_entry")

    async def test_user_menu_exposes_supported_migration(self):
        flow = ConfigFlow()
        flow.hass = self.hass
        menu = await flow.async_step_user()
        self.assertEqual(menu["type"], "menu")
        self.assertIn("migrate", menu["menu_options"])
        form = await flow.async_step_migrate()
        self.assertEqual(form["step_id"], "migrate")

    async def test_failure_after_device_move_rolls_back(self):
        original = self.registry.async_update_entity
        count = 0

        def fail_second_attach(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError("simulated attachment failure")
            return original(*args, **kwargs)

        migration = LegacyMigration(self.hass, self.old, self.new)
        with patch.object(
            type(self.registry), "async_update_entity", side_effect=fail_second_attach
        ):
            with self.assertRaises(ConfigEntryError):
                await migration.prepare()
        for row in (self.sensor, self.weather):
            restored = self.registry.async_get(row.entity_id)
            self.assertEqual(restored.platform, "heweather")
            self.assertEqual(restored.device_id, self.device.id)
        self.assertEqual(
            self.devices.async_get(self.device.id).identifiers,
            {("heweather", "120_0-45_0")},
        )
