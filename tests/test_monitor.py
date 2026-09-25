"""Tests for optional refreshes triggered by other climate entities."""

from __future__ import annotations

import asyncio
import importlib
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, call, patch

PACKAGE_NAME = "_zhong_hong_http_monitor_tests"
PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "zhong_hong_http"


class _GenericEvent:
    """Support the generic Event annotation at module import time."""

    @classmethod
    def __class_getitem__(cls, item: object) -> type[_GenericEvent]:
        return cls


class _State:
    """Minimal Home Assistant state."""

    def __init__(self, state: str, **attributes: object) -> None:
        self.state = state
        self.attributes = attributes


def _load_monitor(registry: object) -> ModuleType:
    """Load the monitor module with minimal Home Assistant stubs."""
    package = ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_PATH)]
    homeassistant = ModuleType("homeassistant")
    homeassistant.__path__ = []
    ha_const = ModuleType("homeassistant.const")
    ha_const.__dict__["ATTR_TEMPERATURE"] = "temperature"
    ha_const.__dict__["EVENT_STATE_CHANGED"] = "state_changed"
    core = ModuleType("homeassistant.core")
    core.__dict__["Event"] = _GenericEvent
    core.__dict__["EventStateChangedData"] = dict
    core.__dict__["HomeAssistant"] = object
    core.__dict__["State"] = _State
    core.__dict__["callback"] = lambda func: func
    helpers = ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    entity_registry = ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.__dict__["async_get"] = lambda hass: registry

    temporary_modules = {
        PACKAGE_NAME: package,
        "homeassistant": homeassistant,
        "homeassistant.const": ha_const,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.entity_registry": entity_registry,
    }
    with patch.dict(sys.modules, temporary_modules):
        return importlib.import_module(f"{PACKAGE_NAME}.monitor")


class OtherClimateMonitorTests(unittest.IsolatedAsyncioTestCase):
    """Test event filtering and recursion prevention."""

    async def test_only_external_control_changes_request_refresh(self) -> None:
        class Registry:
            @staticmethod
            def async_get(entity_id: str) -> object | None:
                if entity_id == "climate.own_unit":
                    return SimpleNamespace(config_entry_id="entry-id")
                return None

        monitor = _load_monitor(Registry())
        debug_patcher = patch.object(monitor.LOGGER, "debug")
        debug_log = debug_patcher.start()
        self.addCleanup(debug_patcher.stop)
        sleep = AsyncMock()
        sleep_patcher = patch.object(monitor.asyncio, "sleep", sleep)
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)
        callbacks: list[Callable[[object], None]] = []
        event_filters: list[Callable[[dict[str, object]], bool]] = []
        tasks: list[asyncio.Task[None]] = []

        class Bus:
            @staticmethod
            def async_listen(
                event_type: str,
                callback: Callable[[object], None],
                *,
                event_filter: Callable[[dict[str, object]], bool],
            ) -> object:
                self.assertEqual(event_type, "state_changed")
                callbacks.append(callback)
                event_filters.append(event_filter)
                return object()

        class Coordinator:
            refresh_count = 0
            last_update_success = True

            async def async_refresh(self) -> None:
                self.refresh_count += 1

        coordinator = Coordinator()
        entry = SimpleNamespace(
            entry_id="entry-id",
            runtime_data=SimpleNamespace(coordinator=coordinator),
            async_create_background_task=lambda hass, coro, name: tasks.append(
                asyncio.create_task(coro, name=name)
            ),
            async_on_unload=lambda unsub: None,
        )
        monitor.async_setup_other_climate_listener(SimpleNamespace(bus=Bus()), entry)
        callback = callbacks[0]
        event_filter = event_filters[0]

        self.assertFalse(
            event_filter(
                {
                    "entity_id": "sensor.room",
                    "old_state": _State("1"),
                    "new_state": _State("2"),
                }
            )
        )
        callback(
            SimpleNamespace(
                data={
                    "entity_id": "climate.external",
                    "old_state": _State("cool", temperature=24, current_temperature=28),
                    "new_state": _State("cool", temperature=24, current_temperature=29),
                }
            )
        )
        callback(
            SimpleNamespace(
                data={
                    "entity_id": "climate.own_unit",
                    "old_state": _State("cool", temperature=24),
                    "new_state": _State("heat", temperature=25),
                }
            )
        )
        callback(
            SimpleNamespace(
                data={
                    "entity_id": "climate.external",
                    "old_state": _State("cool", temperature=24),
                    "new_state": _State("cool", temperature=25),
                }
            )
        )
        callback(
            SimpleNamespace(
                data={
                    "entity_id": "climate.external",
                    "old_state": _State("cool", temperature=25),
                    "new_state": _State("heat", temperature=25),
                }
            )
        )
        await asyncio.gather(*tasks)

        self.assertEqual(coordinator.refresh_count, 4)
        sleep.assert_has_awaits(
            [call(1.0), call(2.0), call(1.0), call(2.0)],
            any_order=True,
        )
        log_templates = [call.args[0] for call in debug_log.call_args_list]
        self.assertIn(
            "Observed climate event for %s: state=%s->%s target_temperature=%s->%s",
            log_templates,
        )
        self.assertIn("Ignoring own climate entity %s", log_templates)
        self.assertIn(
            "Ignoring climate event for %s without a control-state change",
            log_templates,
        )
        self.assertIn(
            "Scheduling indoor-unit refreshes for climate event from %s after %s "
            "seconds",
            log_templates,
        )
        self.assertIn(
            "Refresh request %.1f seconds after climate event from %s completed: "
            "success=%s",
            log_templates,
        )


if __name__ == "__main__":
    unittest.main()
