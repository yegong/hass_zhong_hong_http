"""Explicit device profiles for protocol capabilities not reported by the gateway."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ZhonghongProfile:
    """Capabilities that cannot currently be discovered from the gateway."""

    minimum_temperature: float
    maximum_temperature: float
    target_temperature_step: float


# The EigenStone reference flow uses 16-32 °C, but this still needs direct
# device verification. Keeping it in one profile makes a later profile choice
# migratable without introducing model-specific branches in the climate entity.
DEFAULT_PROFILE = ZhonghongProfile(
    minimum_temperature=16,
    maximum_temperature=32,
    target_temperature_step=1,
)
