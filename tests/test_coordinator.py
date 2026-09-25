"""Tests for command state and delayed readback coordination."""

from __future__ import annotations

import asyncio
import importlib
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import patch

PACKAGE_NAME = "_zhong_hong_http_coordinator_tests"
PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "zhong_hong_http"


class _ConfigEntry:
    """Minimal generic config entry stand-in."""

    @classmethod
    def __class_getitem__(cls, item: object) -> type[_ConfigEntry]:
        return cls


class _DataUpdateCoordinator:
    """Minimal coordinator base used to exercise domain behavior."""

    def __init__(
        self,
        hass: object,
        logger: object,
        *,
        config_entry: object,
        name: str,
        update_interval: object,
        always_update: bool,
    ) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.data: Any = None
        self.last_update_success = True
        self.refresh_count = 0

    @classmethod
    def __class_getitem__(cls, item: object) -> type[_DataUpdateCoordinator]:
        return cls

    async def async_refresh(self) -> None:
        self.refresh_count += 1


class _TranslatedError(Exception):
    """Accept Home Assistant translation keyword arguments."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args)


def _load_modules() -> tuple[ModuleType, ModuleType]:
    """Load coordinator and models with minimal Home Assistant stubs."""
    package = ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_PATH)]
    config_entries = ModuleType("homeassistant.config_entries")
    config_entries.__dict__["ConfigEntry"] = _ConfigEntry
    core = ModuleType("homeassistant.core")
    core.__dict__["HomeAssistant"] = object
    exceptions = ModuleType("homeassistant.exceptions")
    exceptions.__dict__["ConfigEntryAuthFailed"] = _TranslatedError
    exceptions.__dict__["HomeAssistantError"] = _TranslatedError
    exceptions.__dict__["ServiceValidationError"] = _TranslatedError
    helpers = ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    update_coordinator = ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.__dict__["DataUpdateCoordinator"] = _DataUpdateCoordinator
    update_coordinator.__dict__["UpdateFailed"] = _TranslatedError
    homeassistant = ModuleType("homeassistant")
    homeassistant.__path__ = []

    temporary_modules = {
        PACKAGE_NAME: package,
        "homeassistant": homeassistant,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.exceptions": exceptions,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.update_coordinator": update_coordinator,
    }
    with patch.dict(sys.modules, temporary_modules):
        models = importlib.import_module(f"{PACKAGE_NAME}.models")
        coordinator = importlib.import_module(f"{PACKAGE_NAME}.coordinator")
    return coordinator, models


class CoordinatorControlTests(unittest.IsolatedAsyncioTestCase):
    """Test command state selection, failures, and delayed readbacks."""

    async def test_each_command_uses_latest_polled_state(self) -> None:
        coordinator_module, models = _load_modules()
        unit = models.IndoorUnit(1, 1, "Room", False, 1, 24, 28, 2, 0)

        class Client:
            calls: list[tuple[Any, dict[str, Any]]]

            def __init__(self) -> None:
                self.calls = []

            async def async_control(self, base: Any, **changes: Any) -> Any:
                self.calls.append((base, changes))
                return replace(
                    base,
                    is_on=base.is_on if changes["is_on"] is None else changes["is_on"],
                    target_temperature=base.target_temperature
                    if changes["target_temperature"] is None
                    else changes["target_temperature"],
                )

        tasks: list[asyncio.Task[None]] = []
        entry = SimpleNamespace(
            options={},
            async_create_background_task=lambda hass, coro, name: tasks.append(
                asyncio.create_task(coro, name=name)
            ),
        )
        client = Client()
        coordinator = coordinator_module.ZhonghongCoordinator(object(), entry, client)
        coordinator.data = models.GatewayState({unit.key: unit}, 1, ("body-only",))

        with patch.object(coordinator_module, "CONTROL_REFRESH_DELAYS", (0.0,)):
            await coordinator.async_control_unit(unit.key, is_on=True)
            await coordinator.async_control_unit(unit.key, target_temperature=25)
            await asyncio.gather(*tasks)

        self.assertFalse(client.calls[0][0].is_on)
        self.assertFalse(client.calls[1][0].is_on)
        self.assertEqual(coordinator.refresh_count, 2)

    async def test_control_failure_logs_the_specific_cause(self) -> None:
        coordinator_module, models = _load_modules()
        unit = models.IndoorUnit(1, 1, "Room", False, 1, 24, 28, 2, 0)

        class Client:
            async def async_control(self, base: Any, **changes: Any) -> Any:
                raise coordinator_module.ZhonghongTransportError(
                    "gateway returned an empty response"
                )

        entry = SimpleNamespace(options={})
        coordinator = coordinator_module.ZhonghongCoordinator(object(), entry, Client())
        coordinator.data = models.GatewayState({unit.key: unit}, 1, ("body-only",))

        with (
            self.assertLogs(coordinator_module.LOGGER, level="ERROR") as logs,
            self.assertRaises(_TranslatedError),
        ):
            await coordinator.async_control_unit(unit.key, is_on=True)

        output = "\n".join(logs.output)
        self.assertIn("ZhonghongTransportError", output)
        self.assertIn("gateway returned an empty response", output)


if __name__ == "__main__":
    unittest.main()
