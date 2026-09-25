"""Button platform for Zhonghong HTTP."""

from __future__ import annotations

from typing import override

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import ZhonghongConfigEntry, ZhonghongCoordinator

PARALLEL_UPDATES = 1

REFRESH_DESCRIPTION = ButtonEntityDescription(
    key="refresh_indoor_units",
    translation_key="refresh_indoor_units",
    icon="mdi:refresh",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZhonghongConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the gateway refresh button."""
    runtime_data = entry.runtime_data
    async_add_entities(
        [
            ZhonghongRefreshButton(
                runtime_data.coordinator,
                runtime_data.gateway_identifier,
            )
        ]
    )


class ZhonghongRefreshButton(ButtonEntity):
    """Refresh all indoor-unit states and restart their polling interval."""

    _attr_has_entity_name = True
    entity_description = REFRESH_DESCRIPTION

    def __init__(
        self,
        coordinator: ZhonghongCoordinator,
        gateway_identifier: str,
    ) -> None:
        """Initialize the gateway refresh button."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{gateway_identifier}:refresh_indoor_units"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, gateway_identifier)},
        )

    @override
    async def async_press(self) -> None:
        """Immediately refresh indoor units and defer the next scheduled poll."""
        await self._coordinator.async_refresh()
