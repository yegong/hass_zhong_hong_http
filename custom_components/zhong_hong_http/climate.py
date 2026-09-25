"""Climate platform for Zhonghong HTTP."""

from __future__ import annotations

from typing import Any, ClassVar, override

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    MANUFACTURER,
    MODE_COOL,
    MODE_DRY,
    MODE_FAN_ONLY,
    MODE_HEAT,
    MODEL_INDOOR_UNIT,
)
from .coordinator import ZhonghongConfigEntry, ZhonghongCoordinator
from .models import IndoorUnit, UnitKey

PARALLEL_UPDATES = 0

DEVICE_TO_HVAC_MODE: dict[int, HVACMode] = {
    MODE_COOL: HVACMode.COOL,
    MODE_DRY: HVACMode.DRY,
    MODE_FAN_ONLY: HVACMode.FAN_ONLY,
    MODE_HEAT: HVACMode.HEAT,
}
HVAC_MODE_TO_DEVICE: dict[HVACMode, int] = {
    value: key for key, value in DEVICE_TO_HVAC_MODE.items()
}

FAN_MODE_HIGH = "high"
FAN_MODE_MEDIUM = "medium"
FAN_MODE_LOW = "low"
DEVICE_TO_FAN_MODE: dict[int, str] = {
    FAN_HIGH: FAN_MODE_HIGH,
    FAN_MEDIUM: FAN_MODE_MEDIUM,
    FAN_LOW: FAN_MODE_LOW,
}
FAN_MODE_TO_DEVICE: dict[str, int] = {
    value: key for key, value in DEVICE_TO_FAN_MODE.items()
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZhonghongConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up climate entities and discover new indoor units dynamically."""
    runtime_data = entry.runtime_data
    coordinator = runtime_data.coordinator
    known_units: set[UnitKey] = set()

    @callback
    def async_add_new_units() -> None:
        new_keys = coordinator.data.units.keys() - known_units
        if not new_keys:
            return
        async_add_entities(
            ZhonghongClimate(
                coordinator,
                key,
                runtime_data.gateway_identifier,
                runtime_data.gateway_device_id,
            )
            for key in sorted(new_keys)
        )
        known_units.update(new_keys)

    async_add_new_units()
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_units))


class ZhonghongClimate(CoordinatorEntity[ZhonghongCoordinator], ClimateEntity):
    """Representation of one Zhonghong indoor unit."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_WHOLE
    _attr_hvac_modes: ClassVar[list[HVACMode]] = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.HEAT,
    ]
    _attr_fan_modes: ClassVar[list[str]] = [
        FAN_MODE_HIGH,
        FAN_MODE_MEDIUM,
        FAN_MODE_LOW,
    ]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(
        self,
        coordinator: ZhonghongCoordinator,
        key: UnitKey,
        gateway_identifier: str,
        gateway_device_id: str,
    ) -> None:
        """Initialize an indoor-unit climate entity."""
        super().__init__(coordinator)
        self._key = key
        self._last_published_unit = self._unit
        self._last_published_update_success = coordinator.last_update_success
        profile = coordinator.client.profile
        self._attr_min_temp = profile.minimum_temperature
        self._attr_max_temp = profile.maximum_temperature
        self._attr_target_temperature_step = profile.target_temperature_step
        outdoor_unit, indoor_unit = key
        self._attr_unique_id = (
            f"{gateway_identifier}:outdoor:{outdoor_unit}:indoor:{indoor_unit}"
        )
        unit = coordinator.data.units[key]
        device_name = unit.name or f"Indoor unit {outdoor_unit}-{indoor_unit}"
        self._attr_device_info = DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{gateway_identifier}:outdoor:{outdoor_unit}:indoor:{indoor_unit}",
                )
            },
            manufacturer=MANUFACTURER,
            model=MODEL_INDOOR_UNIT,
            name=device_name,
            via_device_id=gateway_device_id,
        )

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Publish only when this unit or its availability actually changed."""
        unit = self._unit
        update_success = self.coordinator.last_update_success
        if (
            unit == self._last_published_unit
            and update_success == self._last_published_update_success
        ):
            return
        self._last_published_unit = unit
        self._last_published_update_success = update_success
        super()._handle_coordinator_update()

    @property
    def _unit(self) -> IndoorUnit | None:
        """Return the latest unit state."""
        return self.coordinator.data.units.get(self._key)

    @property
    @override
    def available(self) -> bool:
        """Return whether the gateway and this indoor unit are available."""
        return super().available and self._unit is not None

    @property
    @override
    def current_temperature(self) -> float | None:
        """Return the measured indoor temperature."""
        return unit.current_temperature if (unit := self._unit) else None

    @property
    @override
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        return unit.target_temperature if (unit := self._unit) else None

    @property
    @override
    def hvac_mode(self) -> HVACMode | None:
        """Return the requested HVAC mode."""
        if (unit := self._unit) is None:
            return None
        if not unit.is_on:
            return HVACMode.OFF
        return DEVICE_TO_HVAC_MODE.get(unit.mode)

    @property
    @override
    def fan_mode(self) -> str | None:
        """Return the current fan mode."""
        return DEVICE_TO_FAN_MODE.get(unit.fan_speed) if (unit := self._unit) else None

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the HVAC mode without enforcing outdoor-unit policy."""
        if hvac_mode is HVACMode.OFF:
            await self.coordinator.async_control_unit(self._key, is_on=False)
            return
        await self.coordinator.async_control_unit(
            self._key,
            is_on=True,
            mode=HVAC_MODE_TO_DEVICE[hvac_mode],
        )

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self.coordinator.async_control_unit(
            self._key,
            target_temperature=float(temperature),
        )

    @override
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan mode."""
        await self.coordinator.async_control_unit(
            self._key,
            fan_speed=FAN_MODE_TO_DEVICE[fan_mode],
        )

    @override
    async def async_turn_on(self) -> None:
        """Turn the indoor unit on using its cached mode."""
        await self.coordinator.async_control_unit(self._key, is_on=True)

    @override
    async def async_turn_off(self) -> None:
        """Turn the indoor unit off."""
        await self.coordinator.async_control_unit(self._key, is_on=False)
