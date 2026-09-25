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
    STATE_SETTLE_REFRESH_DELAYS,
    SUPPORTED_FAN_SPEEDS,
    SUPPORTED_MODES,
)
from .models import GatewayInfo, GatewayState, UnitKey, ZhonghongDataError
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
        """Control a unit and schedule two delayed device readbacks."""
        unit = self.data.units.get(key)
        if unit is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unit_unavailable",
            )
        LOGGER.debug(
            "Controlling indoor unit oa=%s ia=%s idx=%s from polled state: "
            "on=%s mode=%s target_temperature=%s fan=%s",
            unit.outdoor_unit,
            unit.indoor_unit,
            unit.index,
            unit.is_on if is_on is None else is_on,
            unit.mode if mode is None else mode,
            unit.target_temperature
            if target_temperature is None
            else target_temperature,
            unit.fan_speed if fan_speed is None else fan_speed,
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
            LOGGER.exception(
                "Authentication failed while controlling indoor unit oa=%s ia=%s "
                "idx=%s",
                unit.outdoor_unit,
                unit.indoor_unit,
                unit.index,
            )
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
            LOGGER.exception(
                "Failed to control indoor unit oa=%s ia=%s idx=%s",
                unit.outdoor_unit,
                unit.indoor_unit,
                unit.index,
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
            ) from err
        except ValueError as err:
            LOGGER.warning(
                "Rejected control for indoor unit oa=%s ia=%s idx=%s: %s",
                unit.outdoor_unit,
                unit.indoor_unit,
                unit.index,
                err,
            )
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_control_value",
                translation_placeholders={"error": str(err)},
            ) from err

        LOGGER.debug(
            "Gateway accepted control for indoor unit oa=%s ia=%s idx=%s; "
            "scheduling readbacks after %s seconds",
            desired.outdoor_unit,
            desired.indoor_unit,
            desired.index,
            STATE_SETTLE_REFRESH_DELAYS,
        )
        for delay in STATE_SETTLE_REFRESH_DELAYS:
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_refresh_after(delay),
                f"{DOMAIN} control readback after {delay:g}s",
            )

    async def _async_refresh_after(self, delay: float) -> None:
        """Request one coordinator refresh after a control-settling delay."""
        await asyncio.sleep(delay)
        LOGGER.debug("Starting indoor-unit readback after %.1f seconds", delay)
        await self.async_refresh()
        LOGGER.debug(
            "Indoor-unit readback after %.1f seconds completed: success=%s",
            delay,
            self.last_update_success,
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
