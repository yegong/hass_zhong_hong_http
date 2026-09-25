"""Data coordinator for the Zhonghong HTTP integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import ZhonghongApiError, ZhonghongClient
from .const import (
    CONF_SCAN_INTERVAL,
    CONTROL_REFRESH_DELAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    GATEWAY_INFO_INTERVAL,
    SUPPORTED_FAN_SPEEDS,
    SUPPORTED_MODES,
)
from .models import GatewayInfo, GatewayState, IndoorUnit, UnitKey, ZhonghongDataError
from .transport import ZhonghongAuthenticationError, ZhonghongTransportError

LOGGER = logging.getLogger(__name__)


class ZhonghongCoordinator(DataUpdateCoordinator[GatewayState]):
    """Coordinate whole-gateway polling and entity commands."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: ZhonghongClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=config_entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                )
            ),
            always_update=False,
        )
        self.client = client
        self._pending_controls: dict[UnitKey, IndoorUnit] = {}
        self._logged_unknown_modes: set[int] = set()
        self._logged_unknown_fans: set[int] = set()

    async def _async_update_data(self) -> GatewayState:
        """Fetch one complete gateway snapshot."""
        try:
            state = await self.client.async_query_units()
        except ZhonghongAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except (ZhonghongTransportError, ZhonghongApiError, ZhonghongDataError) as err:
            raise UpdateFailed(str(err)) from err
        self._clear_confirmed_controls(state)
        self._log_unknown_values(state)
        return state

    def _clear_confirmed_controls(self, state: GatewayState) -> None:
        """Discard pending command states once their control fields are read back."""
        for key, pending in tuple(self._pending_controls.items()):
            current = state.units.get(key)
            if current is not None and _same_control_state(current, pending):
                self._pending_controls.pop(key, None)

    def _log_unknown_values(self, state: GatewayState) -> None:
        """Log each unsupported device enum once without changing its meaning."""
        for unit in state.units.values():
            if (
                unit.mode not in SUPPORTED_MODES
                and unit.mode not in self._logged_unknown_modes
            ):
                LOGGER.warning(
                    "Gateway returned unsupported HVAC mode %s; affected units "
                    "will expose no active HVAC mode",
                    unit.mode,
                )
                self._logged_unknown_modes.add(unit.mode)
            if (
                unit.fan_speed not in SUPPORTED_FAN_SPEEDS
                and unit.fan_speed not in self._logged_unknown_fans
            ):
                LOGGER.warning(
                    "Gateway returned unsupported fan speed %s; affected units "
                    "will expose no fan mode",
                    unit.fan_speed,
                )
                self._logged_unknown_fans.add(unit.fan_speed)

    async def async_control_unit(
        self,
        key: UnitKey,
        *,
        is_on: bool | None = None,
        mode: int | None = None,
        target_temperature: float | None = None,
        fan_speed: int | None = None,
    ) -> None:
        """Control a unit and schedule two delayed device readbacks."""
        unit = self._pending_controls.get(key) or self.data.units.get(key)
        if unit is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unit_unavailable",
            )
        try:
            desired = await self.client.async_control(
                unit,
                is_on=is_on,
                mode=mode,
                target_temperature=target_temperature,
                fan_speed=fan_speed,
            )
        except ZhonghongAuthenticationError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except (
            ZhonghongTransportError,
            ZhonghongApiError,
            ZhonghongDataError,
        ) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
            ) from err
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_control_value",
                translation_placeholders={"error": str(err)},
            ) from err

        self._pending_controls[key] = desired
        for delay in CONTROL_REFRESH_DELAYS:
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_refresh_after(delay),
                f"{DOMAIN} control readback after {delay:g}s",
            )

    async def _async_refresh_after(self, delay: float) -> None:
        """Request one coordinator refresh after a control-settling delay."""
        await asyncio.sleep(delay)
        await self.async_refresh()


def _same_control_state(first: IndoorUnit, second: IndoorUnit) -> bool:
    """Return whether two snapshots have identical writable control fields."""
    return (
        first.is_on == second.is_on
        and first.mode == second.mode
        and first.target_temperature == second.target_temperature
        and first.fan_speed == second.fan_speed
        and first.index == second.index
    )


class ZhonghongGatewayCoordinator(DataUpdateCoordinator[GatewayInfo]):
    """Poll slow-changing VRF gateway information every five minutes."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: ZhonghongClient,
    ) -> None:
        """Initialize the gateway information coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_gateway",
            update_interval=GATEWAY_INFO_INTERVAL,
            always_update=False,
        )
        self.client = client

    async def _async_update_data(self) -> GatewayInfo:
        """Fetch gateway identity, version, and error codes."""
        try:
            return await self.client.async_query_gateway_info()
        except ZhonghongAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except (ZhonghongTransportError, ZhonghongApiError, ZhonghongDataError) as err:
            raise UpdateFailed(str(err)) from err


@dataclass(slots=True)
class ZhonghongRuntimeData:
    """Runtime objects owned by one config entry."""

    client: ZhonghongClient
    coordinator: ZhonghongCoordinator
    gateway_coordinator: ZhonghongGatewayCoordinator
    gateway_identifier: str
    gateway_device_id: str


type ZhonghongConfigEntry = ConfigEntry[ZhonghongRuntimeData]
