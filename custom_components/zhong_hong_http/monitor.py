"""Optional refresh triggers from other Home Assistant climate entities."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import ATTR_TEMPERATURE, EVENT_STATE_CHANGED
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .coordinator import ZhonghongConfigEntry


@callback
def async_setup_other_climate_listener(
    hass: HomeAssistant,
    entry: ZhonghongConfigEntry,
) -> None:
    """Refresh indoor units when another integration's climate control changes."""
    entity_registry = er.async_get(hass)
    LOGGER.debug("Enabled refresh listener for other climate entities")

    @callback
    def _is_climate_event(event_data: EventStateChangedData) -> bool:
        return event_data["entity_id"].startswith("climate.")

    @callback
    def _handle_climate_change(event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        LOGGER.debug(
            "Observed climate event for %s: state=%s->%s target_temperature=%s->%s",
            entity_id,
            old_state.state if old_state is not None else None,
            new_state.state if new_state is not None else None,
            old_state.attributes.get(ATTR_TEMPERATURE)
            if old_state is not None
            else None,
            new_state.attributes.get(ATTR_TEMPERATURE)
            if new_state is not None
            else None,
        )
        registry_entry = entity_registry.async_get(entity_id)
        if (
            registry_entry is not None
            and registry_entry.config_entry_id == entry.entry_id
        ):
            LOGGER.debug("Ignoring own climate entity %s", entity_id)
            return

        if old_state is None or new_state is None:
            LOGGER.debug(
                "Ignoring climate event for %s without both old and new state",
                entity_id,
            )
            return
        if not climate_control_state_changed(old_state, new_state):
            LOGGER.debug(
                "Ignoring climate event for %s without a control-state change",
                entity_id,
            )
            return

        LOGGER.debug(
            "Requesting indoor-unit refresh for climate event from %s", entity_id
        )
        entry.async_create_background_task(
            hass,
            _async_request_refresh(entry, entity_id),
            f"{DOMAIN} refresh after external climate change",
        )

    entry.async_on_unload(
        hass.bus.async_listen(
            EVENT_STATE_CHANGED,
            _handle_climate_change,
            event_filter=_is_climate_event,
        )
    )


async def _async_request_refresh(
    entry: ZhonghongConfigEntry,
    source_entity_id: str,
) -> None:
    """Request and log a refresh caused by another climate entity."""
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_request_refresh()
    LOGGER.debug(
        "Refresh request for climate event from %s completed: success=%s",
        source_entity_id,
        coordinator.last_update_success,
    )


def climate_control_state_changed(old_state: State, new_state: State) -> bool:
    """Return whether a climate's HVAC state or target temperature changed."""
    return old_state.state != new_state.state or old_state.attributes.get(
        ATTR_TEMPERATURE
    ) != new_state.attributes.get(ATTR_TEMPERATURE)
