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
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    GATEWAY_INFO_INTERVAL,
    SUPPORTED_FAN_SPEEDS,
    SUPPORTED_MODES,
)
from .models import GatewayInfo, GatewayState, UnitKey, ZhonghongDataError
from .transport import ZhonghongAuthenticationError, ZhonghongTransportError

LOGGER = logging.getLogger(__name__)


class ZhonghongCoordinator(DataUpdateCoordinator[GatewayState]):
    """Coordinate whole-gateway polling and serialized entity commands."""

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
        self._command_lock = asyncio.Lock()
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
        self._log_unknown_values(state)
        return state

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
        """Control a unit from its latest state, then refresh the whole gateway."""
        async with self._command_lock:
            unit = self.data.units.get(key)
            if unit is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unit_unavailable",
                )
            try:
                await self.client.async_control(
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

            # Complete the device readback while holding the command lock so a
            # following read-modify-write command cannot use stale cached data.
            await self.async_refresh()


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
