"""Protocol client for the Zhonghong HTTP gateway."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import replace
from typing import Any

from .const import (
    MAX_PAGES,
    QUERY_TIMEOUT,
    SUPPORTED_FAN_SPEEDS,
    SUPPORTED_MODES,
)
from .models import (
    GatewayInfo,
    GatewayState,
    IndoorUnit,
    UnitKey,
    ZhonghongDataError,
    parse_gateway_info,
    parse_indoor_unit,
)
from .profile import DEFAULT_PROFILE, ZhonghongProfile
from .transport import (
    ZhonghongAuthenticationError,
    ZhonghongTransport,
    ZhonghongTransportError,
)


class ZhonghongApiError(Exception):
    """Raised when a gateway returns an unsuccessful API result."""


def _parse_payload(body: bytes) -> dict[str, Any]:
    """Decode and validate a gateway JSON envelope."""
    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ZhonghongDataError("gateway response is not valid UTF-8 JSON") from err
    if not isinstance(payload, dict):
        raise ZhonghongDataError("gateway JSON response must be an object")

    error_value = payload.get("err")
    success = error_value == "0" or (type(error_value) is int and error_value == 0)
    if not success:
        raise ZhonghongApiError(
            f"gateway reported an unsuccessful result: err={error_value!r}"
        )
    return payload


class ZhonghongClient:
    """Asynchronous, serialized access to one Zhonghong gateway."""

    def __init__(
        self,
        transport: ZhonghongTransport,
        profile: ZhonghongProfile = DEFAULT_PROFILE,
    ) -> None:
        """Initialize the client."""
        self.transport = transport
        self.profile = profile
        self._request_lock = asyncio.Lock()

    async def async_query_units(self) -> GatewayState:
        """Read and atomically validate all indoor-unit pages."""
        async with self._request_lock:
            try:
                async with asyncio.timeout(QUERY_TIMEOUT):
                    return await self._async_query_units_locked()
            except TimeoutError as err:
                raise ZhonghongTransportError(
                    f"gateway query timed out after {QUERY_TIMEOUT:g} seconds"
                ) from err

    async def _async_query_units_locked(self) -> GatewayState:
        """Read all pages while the caller holds the request lock."""
        units: dict[UnitKey, IndoorUnit] = {}
        indexes: set[int] = set()
        transports: list[str] = []
        page_fingerprints: set[str] = set()

        for page in range(MAX_PAGES):
            response = await self.transport.async_request((("f", 17), ("p", page)))
            transports.append(response.transport)
            payload = _parse_payload(response.body)
            raw_units = payload.get("unit")
            if not isinstance(raw_units, list):
                raise ZhonghongDataError("gateway field 'unit' must be an array")
            if not raw_units:
                return GatewayState(units, page + 1, tuple(transports))

            fingerprint = json.dumps(
                raw_units,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if fingerprint in page_fingerprints:
                raise ZhonghongDataError("gateway repeated a page of indoor-unit data")
            page_fingerprints.add(fingerprint)

            for raw_unit in raw_units:
                unit = parse_indoor_unit(raw_unit)
                if unit.key in units:
                    raise ZhonghongDataError(
                        "gateway returned a duplicate indoor-unit identity"
                    )
                if unit.index in indexes:
                    raise ZhonghongDataError(
                        "gateway returned a duplicate indoor-unit index"
                    )
                units[unit.key] = unit
                indexes.add(unit.index)

        raise ZhonghongDataError(
            f"gateway did not return an empty page within {MAX_PAGES} pages"
        )

    async def async_query_gateway_info(self) -> GatewayInfo:
        """Read the slow-changing VRF gateway identity and error codes."""
        async with self._request_lock:
            response = await self.transport.async_request((("f", 1),))
            return parse_gateway_info(_parse_payload(response.body))

    async def async_control(
        self,
        unit: IndoorUnit,
        *,
        is_on: bool | None = None,
        mode: int | None = None,
        target_temperature: float | None = None,
        fan_speed: int | None = None,
    ) -> None:
        """Submit a complete control state for one indoor unit."""
        requested_temperature = (
            None if target_temperature is None else float(target_temperature)
        )
        desired = replace(
            unit,
            is_on=unit.is_on if is_on is None else is_on,
            mode=unit.mode if mode is None else mode,
            target_temperature=(
                unit.target_temperature
                if requested_temperature is None
                else requested_temperature
            ),
            fan_speed=unit.fan_speed if fan_speed is None else fan_speed,
        )
        if mode is not None and mode not in SUPPORTED_MODES:
            raise ValueError(f"unsupported mode value: {desired.mode}")
        if fan_speed is not None and fan_speed not in SUPPORTED_FAN_SPEEDS:
            raise ValueError(f"unsupported fan value: {desired.fan_speed}")
        if requested_temperature is not None:
            if not (
                self.profile.minimum_temperature
                <= requested_temperature
                <= self.profile.maximum_temperature
            ):
                raise ValueError(
                    "target temperature must be between "
                    f"{self.profile.minimum_temperature:g} and "
                    f"{self.profile.maximum_temperature:g}"
                )
            offset = requested_temperature - self.profile.minimum_temperature
            steps = offset / self.profile.target_temperature_step
            if not math.isclose(steps, round(steps), abs_tol=1e-9):
                raise ValueError("target temperature does not match the supported step")

        temperature_parameter: str | int = (
            int(desired.target_temperature)
            if desired.target_temperature.is_integer()
            else format(desired.target_temperature, "g")
        )

        parameters: tuple[tuple[str, str | int], ...] = (
            ("f", 18),
            ("on", int(desired.is_on)),
            ("mode", desired.mode),
            ("tempSet", temperature_parameter),
            ("fan", desired.fan_speed),
            ("idx", desired.index),
        )
        async with self._request_lock:
            response = await self.transport.async_request(parameters)
            _parse_payload(response.body)


__all__ = [
    "ZhonghongApiError",
    "ZhonghongAuthenticationError",
    "ZhonghongClient",
]
