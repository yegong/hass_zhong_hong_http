"""Config flow for Zhonghong HTTP."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, override

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import ZhonghongApiError, ZhonghongClient
from .const import (
    CONF_REFRESH_ON_OTHER_CLIMATE_CHANGES,
    CONF_SCAN_INTERVAL,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_REFRESH_ON_OTHER_CLIMATE_CHANGES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .models import GatewayInfo, ZhonghongDataError
from .transport import (
    Endpoint,
    ZhonghongAuthenticationError,
    ZhonghongTransport,
    ZhonghongTransportError,
)

LOGGER = logging.getLogger(__name__)


def _scan_interval_selector() -> NumberSelector:
    """Return the bounded indoor-unit polling interval selector."""
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_SCAN_INTERVAL,
            max=MAX_SCAN_INTERVAL,
            step=1,
            mode=NumberSelectorMode.SLIDER,
            unit_of_measurement=UnitOfTime.SECONDS,
        )
    )


def _schema(
    defaults: dict[str, Any] | None = None,
    *,
    include_scan_interval: bool = False,
) -> probatio.Schema:
    """Build a config schema with suggested defaults."""
    defaults = defaults or {}
    fields: dict[Any, Any] = {
        probatio.Required(
            CONF_HOST,
            default=defaults.get(CONF_HOST, probatio.UNDEFINED),
        ): str,
        probatio.Required(
            CONF_PORT,
            default=defaults.get(CONF_PORT, DEFAULT_PORT),
        ): int,
        probatio.Required(
            CONF_USERNAME,
            default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME),
        ): str,
        probatio.Optional(
            CONF_PASSWORD,
            default=defaults.get(CONF_PASSWORD, DEFAULT_PASSWORD),
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
    if include_scan_interval:
        fields[
            probatio.Required(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            )
        ] = _scan_interval_selector()
        fields[
            probatio.Required(
                CONF_REFRESH_ON_OTHER_CLIMATE_CHANGES,
                default=defaults.get(
                    CONF_REFRESH_ON_OTHER_CLIMATE_CHANGES,
                    DEFAULT_REFRESH_ON_OTHER_CLIMATE_CHANGES,
                ),
            )
        ] = BooleanSelector()
    return probatio.Schema(fields)


def _normalize_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize config input before validation and storage."""
    return {
        CONF_HOST: user_input[CONF_HOST].strip(),
        CONF_PORT: int(user_input[CONF_PORT]),
        CONF_USERNAME: user_input[CONF_USERNAME],
        CONF_PASSWORD: user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD),
    }


async def _async_validate_input(data: dict[str, Any]) -> GatewayInfo:
    """Validate credentials and the minimum gateway response contract."""
    endpoint = Endpoint(data[CONF_HOST], data[CONF_PORT])
    client = ZhonghongClient(
        ZhonghongTransport(
            endpoint,
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
        )
    )
    gateway_info = await client.async_query_gateway_info()
    await client.async_query_units()
    return gateway_info


class ZhonghongHttpConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Zhonghong HTTP config flow."""

    VERSION = 1

    async def _async_validate_form(
        self,
        user_input: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, GatewayInfo | None, dict[str, str]]:
        """Normalize and validate one user-submitted form."""
        errors: dict[str, str] = {}
        try:
            data = _normalize_input(user_input)
            gateway_info = await _async_validate_input(data)
        except ZhonghongAuthenticationError:
            errors["base"] = "invalid_auth"
        except ZhonghongDataError:
            errors["base"] = "invalid_response"
        except ValueError:
            errors["base"] = "invalid_host"
        except (ZhonghongTransportError, ZhonghongApiError):
            errors["base"] = "cannot_connect"
        except Exception:
            LOGGER.exception("Unexpected exception validating Zhonghong gateway")
            errors["base"] = "unknown"
        else:
            return data, gateway_info, errors
        return None, None, errors

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for polling settings."""
        return ZhonghongOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle setup initiated by the user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, gateway_info, errors = await self._async_validate_form(user_input)
            if data is not None and gateway_info is not None:
                await self.async_set_unique_id(gateway_info.device_id)
                self._abort_if_unique_id_configured()
                self._async_abort_entries_match(
                    {CONF_HOST: data[CONF_HOST], CONF_PORT: data[CONF_PORT]}
                )
                return self.async_create_entry(
                    title=f"Zhonghong VRF ({data[CONF_HOST]})",
                    data=data,
                    options={
                        CONF_SCAN_INTERVAL: int(
                            user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                        ),
                        CONF_REFRESH_ON_OTHER_CLIMATE_CHANGES: bool(
                            user_input.get(
                                CONF_REFRESH_ON_OTHER_CLIMATE_CHANGES,
                                DEFAULT_REFRESH_ON_OTHER_CLIMATE_CHANGES,
                            )
                        ),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input, include_scan_interval=True),
            errors=errors,
        )

    @override
    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update connection settings for an existing gateway."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, gateway_info, errors = await self._async_validate_form(user_input)
            if data is not None and gateway_info is not None:
                await self.async_set_unique_id(gateway_info.device_id)
                self._abort_if_unique_id_mismatch()
                self._async_abort_entries_match(
                    {CONF_HOST: data[CONF_HOST], CONF_PORT: data[CONF_PORT]}
                )
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=data,
                    title=f"Zhonghong VRF ({data[CONF_HOST]})",
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    @override
    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for an existing gateway."""
        return await self.async_step_reauth_confirm()

    @override
    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and save replacement credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = dict(entry.data)
            data.update(user_input)
            validated, gateway_info, errors = await self._async_validate_form(data)
            if validated is not None and gateway_info is not None:
                await self.async_set_unique_id(gateway_info.device_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: validated[CONF_USERNAME],
                        CONF_PASSWORD: validated[CONF_PASSWORD],
                    },
                )

        credential_defaults = {
            CONF_USERNAME: entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
            CONF_PASSWORD: entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
        }
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_USERNAME,
                        default=credential_defaults[CONF_USERNAME],
                    ): str,
                    probatio.Optional(
                        CONF_PASSWORD,
                        default=credential_defaults[CONF_PASSWORD],
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                }
            ),
            errors=errors,
        )


class ZhonghongOptionsFlow(OptionsFlowWithReload):
    """Configure indoor-unit refresh behavior."""

    @override
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_REFRESH_ON_OTHER_CLIMATE_CHANGES: bool(
                        user_input[CONF_REFRESH_ON_OTHER_CLIMATE_CHANGES]
                    ),
                },
            )

        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): _scan_interval_selector(),
                    probatio.Required(
                        CONF_REFRESH_ON_OTHER_CLIMATE_CHANGES,
                        default=self.config_entry.options.get(
                            CONF_REFRESH_ON_OTHER_CLIMATE_CHANGES,
                            DEFAULT_REFRESH_ON_OTHER_CLIMATE_CHANGES,
                        ),
                    ): BooleanSelector(),
                }
            ),
        )
