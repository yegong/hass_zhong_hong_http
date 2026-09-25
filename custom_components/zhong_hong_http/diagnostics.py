"""Diagnostics for Zhonghong HTTP."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import ZhonghongConfigEntry

TO_REDACT = {CONF_HOST, CONF_USERNAME, CONF_PASSWORD, "device_id", "name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ZhonghongConfigEntry,
) -> dict[str, Any]:
    """Return credential- and room-name-safe diagnostics."""
    state = entry.runtime_data.coordinator.data
    gateway_info = entry.runtime_data.gateway_coordinator.data
    units = [
        {
            "outdoor_unit": unit.outdoor_unit,
            "indoor_unit": unit.indoor_unit,
            "name": unit.name,
            "is_on": unit.is_on,
            "mode": unit.mode,
            "target_temperature": unit.target_temperature,
            "current_temperature": unit.current_temperature,
            "fan_speed": unit.fan_speed,
            "index": unit.index,
        }
        for unit in state.units.values()
    ]
    return {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "last_update_success": entry.runtime_data.coordinator.last_update_success,
        "gateway_info": async_redact_data(
            {
                "model_code": gateway_info.model_code,
                "software_version": gateway_info.software_version,
                "device_id": gateway_info.device_id,
                "hardware_error_code": gateway_info.hardware_error_code,
                "module_error_code": gateway_info.module_error_code,
                "last_update_success": (
                    entry.runtime_data.gateway_coordinator.last_update_success
                ),
            },
            TO_REDACT,
        ),
        "page_count": state.page_count,
        "response_transports": state.response_transports,
        "units": async_redact_data(units, TO_REDACT),
    }
