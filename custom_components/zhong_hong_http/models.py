"""Domain models and response validation for Zhonghong HTTP."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, TypeAlias

UnitKey: TypeAlias = tuple[int, int]


class ZhonghongDataError(ValueError):
    """Raised when gateway data does not satisfy the protocol contract."""


@dataclass(frozen=True, slots=True)
class GatewayInfo:
    """Slow-changing identity and status data for the VRF gateway."""

    model_code: str
    software_version: str
    device_id: str
    hardware_error_code: str
    module_error_code: str


@dataclass(frozen=True, slots=True)
class IndoorUnit:
    """State of one VRF indoor unit."""

    outdoor_unit: int
    indoor_unit: int
    name: str
    is_on: bool
    mode: int
    target_temperature: float
    current_temperature: float
    fan_speed: int
    index: int

    @property
    def key(self) -> UnitKey:
        """Return the unit identity within a gateway."""
        return (self.outdoor_unit, self.indoor_unit)


@dataclass(frozen=True, slots=True)
class GatewayState:
    """Complete, atomically published gateway state."""

    units: dict[UnitKey, IndoorUnit]
    page_count: int = field(compare=False)
    response_transports: tuple[str, ...] = field(compare=False)


def _required_int(payload: dict[str, Any], field: str) -> int:
    """Return a required protocol integer without accepting booleans."""
    value = payload.get(field)
    if type(value) is not int:
        raise ZhonghongDataError(f"field {field!r} must be an integer")
    return value


def _required_temperature(payload: dict[str, Any], field: str) -> float:
    """Return a finite numeric temperature from a string or number."""
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ZhonghongDataError(f"field {field!r} must be numeric")
    try:
        result = float(value)
    except ValueError as err:
        raise ZhonghongDataError(f"field {field!r} must be numeric") from err
    if not math.isfinite(result):
        raise ZhonghongDataError(f"field {field!r} must be finite")
    return result


def _required_string(payload: dict[str, Any], field: str) -> str:
    """Return a required protocol string with surrounding padding removed."""
    value = payload.get(field)
    if not isinstance(value, str):
        raise ZhonghongDataError(f"field {field!r} must be a string")
    return value.strip()


def parse_gateway_info(payload: dict[str, Any]) -> GatewayInfo:
    """Validate the response returned by the ``f=1`` endpoint."""
    device_id = _required_string(payload, "id")
    if not device_id:
        raise ZhonghongDataError("field 'id' must not be empty")
    return GatewayInfo(
        model_code=_required_string(payload, "model"),
        software_version=_required_string(payload, "sw"),
        device_id=device_id,
        hardware_error_code=_required_string(payload, "hwerror"),
        module_error_code=_required_string(payload, "moduleerror"),
    )


def parse_indoor_unit(payload: Any) -> IndoorUnit:
    """Validate and convert an indoor-unit object returned by the gateway."""
    if not isinstance(payload, dict):
        raise ZhonghongDataError("every item in 'unit' must be an object")

    on_value = _required_int(payload, "on")
    if on_value not in (0, 1):
        raise ZhonghongDataError("field 'on' must be 0 or 1")

    name = payload.get("nm", "")
    if not isinstance(name, str):
        raise ZhonghongDataError("field 'nm' must be a string")

    return IndoorUnit(
        outdoor_unit=_required_int(payload, "oa"),
        indoor_unit=_required_int(payload, "ia"),
        name=name.strip(),
        is_on=bool(on_value),
        mode=_required_int(payload, "mode"),
        target_temperature=_required_temperature(payload, "tempSet"),
        current_temperature=_required_temperature(payload, "tempIn"),
        fan_speed=_required_int(payload, "fan"),
        index=_required_int(payload, "idx"),
    )
