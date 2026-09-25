"""Optional refresh triggers from other Home Assistant climate entities."""

from __future__ import annotations

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

if TYPE_CHECKING:
    from .coordinator import ZhonghongConfigEntry


@callback
def async_setup_other_climate_listener(
    hass: HomeAssistant,
    entry: ZhonghongConfigEntry,
) -> None:
    """Refresh indoor units when another integration's climate control changes."""
    entity_registry = er.async_get(hass)

    @callback
    def _is_climate_event(event_data: EventStateChangedData) -> bool:
        return event_data["entity_id"].startswith("climate.")

    @callback
    def _handle_climate_change(event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        registry_entry = entity_registry.async_get(entity_id)
        if (
            registry_entry is not None
            and registry_entry.config_entry_id == entry.entry_id
        ):
            return

        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        if (
            old_state is None
            or new_state is None
            or not climate_control_state_changed(old_state, new_state)
        ):
            return

        entry.async_create_background_task(
            hass,
            entry.runtime_data.coordinator.async_request_refresh(),
            f"{DOMAIN} refresh after external climate change",
        )

    entry.async_on_unload(
        hass.bus.async_listen(
            EVENT_STATE_CHANGED,
            _handle_climate_change,
            event_filter=_is_climate_event,
        )
    )


def climate_control_state_changed(old_state: State, new_state: State) -> bool:
    """Return whether a climate's HVAC state or target temperature changed."""
    return old_state.state != new_state.state or old_state.attributes.get(
        ATTR_TEMPERATURE
    ) != new_state.attributes.get(ATTR_TEMPERATURE)
