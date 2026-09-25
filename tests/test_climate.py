"""Tests for per-indoor-unit climate state publication."""

from __future__ import annotations

import importlib
import sys
import unittest
from dataclasses import replace
from enum import Enum, IntFlag
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import patch

PACKAGE_NAME = "_zhong_hong_http_climate_tests"
PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "zhong_hong_http"


class _ClimateEntity:
    """Minimal climate base."""


class _ClimateEntityFeature(IntFlag):
    TARGET_TEMPERATURE = 1
    FAN_MODE = 2
    TURN_ON = 4
    TURN_OFF = 8


class _HVACMode(Enum):
    OFF = "off"
    COOL = "cool"
    DRY = "dry"
    FAN_ONLY = "fan_only"
    HEAT = "heat"


class _CoordinatorEntity:
    """Record coordinator-driven state writes."""

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self.write_count = 0

    @classmethod
    def __class_getitem__(cls, item: object) -> type[_CoordinatorEntity]:
        return cls

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success)

    def _handle_coordinator_update(self) -> None:
        self.write_count += 1


def _load_modules() -> tuple[ModuleType, ModuleType]:
    """Load climate and models with minimal Home Assistant stubs."""
    package = ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_PATH)]
    homeassistant = ModuleType("homeassistant")
    homeassistant.__path__ = []
    components = ModuleType("homeassistant.components")
    components.__path__ = []
    climate_base = ModuleType("homeassistant.components.climate")
    climate_base.__dict__["ClimateEntity"] = _ClimateEntity
    climate_base.__dict__["ClimateEntityFeature"] = _ClimateEntityFeature
    climate_base.__dict__["HVACMode"] = _HVACMode
    ha_const = ModuleType("homeassistant.const")
    ha_const.__dict__["ATTR_TEMPERATURE"] = "temperature"
    ha_const.__dict__["PRECISION_WHOLE"] = 1
    ha_const.__dict__["UnitOfTemperature"] = SimpleNamespace(CELSIUS="°C")
    core = ModuleType("homeassistant.core")
    core.__dict__["HomeAssistant"] = object
    core.__dict__["callback"] = lambda func: func
    helpers = ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    device_registry = ModuleType("homeassistant.helpers.device_registry")
    device_registry.__dict__["DeviceInfo"] = lambda **values: values
    entity_platform = ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.__dict__["AddConfigEntryEntitiesCallback"] = object
    update_coordinator = ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.__dict__["CoordinatorEntity"] = _CoordinatorEntity
    coordinator = ModuleType(f"{PACKAGE_NAME}.coordinator")
    coordinator.__dict__["ZhonghongConfigEntry"] = object
    coordinator.__dict__["ZhonghongCoordinator"] = object

    temporary_modules = {
        PACKAGE_NAME: package,
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.climate": climate_base,
        "homeassistant.const": ha_const,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.device_registry": device_registry,
        "homeassistant.helpers.entity_platform": entity_platform,
        "homeassistant.helpers.update_coordinator": update_coordinator,
        f"{PACKAGE_NAME}.coordinator": coordinator,
    }
    with patch.dict(sys.modules, temporary_modules):
        models = importlib.import_module(f"{PACKAGE_NAME}.models")
        climate = importlib.import_module(f"{PACKAGE_NAME}.climate")
    return climate, models


class ClimatePublicationTests(unittest.TestCase):
    """Test that one unit's change does not publish all climate entities."""

    def test_only_changed_unit_and_availability_are_published(self) -> None:
        climate, models = _load_modules()
        first = models.IndoorUnit(1, 1, "One", False, 1, 24, 28, 2, 0)
        second = models.IndoorUnit(1, 2, "Two", False, 1, 24, 28, 2, 1)
        coordinator = SimpleNamespace(
            data=models.GatewayState(
                {first.key: first, second.key: second}, 1, ("body-only",)
            ),
            last_update_success=True,
            client=SimpleNamespace(
                profile=SimpleNamespace(
                    minimum_temperature=16,
                    maximum_temperature=32,
                    target_temperature_step=1,
                )
            ),
        )
        first_entity = climate.ZhonghongClimate(
            coordinator, first.key, "gateway:test", "gateway-device"
        )
        second_entity = climate.ZhonghongClimate(
            coordinator, second.key, "gateway:test", "gateway-device"
        )

        coordinator.data = models.GatewayState(
            {first.key: replace(first, is_on=True), second.key: second},
            2,
            ("standard", "standard"),
        )
        first_entity._handle_coordinator_update()
        second_entity._handle_coordinator_update()

        self.assertEqual(first_entity.write_count, 1)
        self.assertEqual(second_entity.write_count, 0)

        coordinator.last_update_success = False
        first_entity._handle_coordinator_update()
        second_entity._handle_coordinator_update()

        self.assertEqual(first_entity.write_count, 2)
        self.assertEqual(second_entity.write_count, 1)


if __name__ == "__main__":
    unittest.main()
