"""Tests for the gateway refresh button without a Home Assistant install."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


@dataclass(frozen=True)
class _ButtonEntityDescription:
    """Minimal stand-in for Home Assistant's entity description."""

    key: str
    translation_key: str
    icon: str


class _ButtonEntity:
    """Minimal stand-in for Home Assistant's button base class."""


def _device_info(**values: object) -> dict[str, object]:
    """Return device info in the same mapping shape used by Home Assistant."""
    return values


def _load_button_module() -> ModuleType:
    """Load the platform with minimal HA modules isolated to this import."""
    package_name = "_zhong_hong_http_button_tests"
    component_path = Path(__file__).parents[1] / "custom_components" / "zhong_hong_http"
    package = ModuleType(package_name)
    package.__path__ = [str(component_path)]

    homeassistant = ModuleType("homeassistant")
    homeassistant.__path__ = []
    components = ModuleType("homeassistant.components")
    components.__path__ = []
    button = ModuleType("homeassistant.components.button")
    button.__dict__["ButtonEntity"] = _ButtonEntity
    button.__dict__["ButtonEntityDescription"] = _ButtonEntityDescription
    core = ModuleType("homeassistant.core")
    core.__dict__["HomeAssistant"] = object
    helpers = ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    device_registry = ModuleType("homeassistant.helpers.device_registry")
    device_registry.__dict__["DeviceInfo"] = _device_info
    entity_platform = ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.__dict__["AddConfigEntryEntitiesCallback"] = object
    coordinator = ModuleType(f"{package_name}.coordinator")
    coordinator.__dict__["ZhonghongConfigEntry"] = object
    coordinator.__dict__["ZhonghongCoordinator"] = object

    temporary_modules = {
        package_name: package,
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.button": button,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.device_registry": device_registry,
        "homeassistant.helpers.entity_platform": entity_platform,
        f"{package_name}.coordinator": coordinator,
    }
    module_name = f"{package_name}.button"
    spec = importlib.util.spec_from_file_location(
        module_name,
        component_path / "button.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load button platform")
    module = importlib.util.module_from_spec(spec)
    temporary_modules[module_name] = module
    with patch.dict(sys.modules, temporary_modules):
        spec.loader.exec_module(module)
    return module


class RefreshButtonTests(unittest.IsolatedAsyncioTestCase):
    """Test the public refresh action and gateway device association."""

    async def test_press_refreshes_only_the_indoor_unit_coordinator(self) -> None:
        module = _load_button_module()

        class Coordinator:
            refresh_count = 0

            async def async_refresh(self) -> None:
                self.refresh_count += 1

        coordinator = Coordinator()
        entity = module.ZhonghongRefreshButton(coordinator, "gateway:test-id")

        await entity.async_press()

        self.assertEqual(coordinator.refresh_count, 1)
        self.assertEqual(
            entity._attr_unique_id,
            "gateway:test-id:refresh_indoor_units",
        )
        self.assertEqual(
            entity._attr_device_info["identifiers"],
            {("zhong_hong_http", "gateway:test-id")},
        )

    async def test_setup_adds_one_gateway_button(self) -> None:
        module = _load_button_module()
        coordinator = SimpleNamespace(async_refresh=None)
        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(
                coordinator=coordinator,
                gateway_identifier="gateway:test-id",
            )
        )
        entities: list[object] = []

        await module.async_setup_entry(object(), entry, entities.extend)

        self.assertEqual(len(entities), 1)


if __name__ == "__main__":
    unittest.main()
