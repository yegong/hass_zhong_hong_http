"""The Zhonghong HTTP integration."""

from __future__ import annotations

import logging

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .client import ZhonghongClient
from .const import (
    CONF_REFRESH_ON_OTHER_CLIMATE_CHANGES,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_REFRESH_ON_OTHER_CLIMATE_CHANGES,
    DEFAULT_USERNAME,
    DOMAIN,
    MANUFACTURER,
    MODEL_GATEWAY,
)
from .coordinator import (
    ZhonghongConfigEntry,
    ZhonghongCoordinator,
    ZhonghongGatewayCoordinator,
    ZhonghongRuntimeData,
)
from .monitor import async_setup_other_climate_listener
from .transport import Endpoint, ZhonghongTransport

LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.CLIMATE]


async def async_setup_entry(hass: HomeAssistant, entry: ZhonghongConfigEntry) -> bool:
    """Set up Zhonghong HTTP from a config entry."""
    endpoint = Endpoint(
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
    )
    transport = ZhonghongTransport(
        endpoint,
        entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
        entry.data.get(CONF_PASSWORD, DEFAULT_PASSWORD),
    )
    client = ZhonghongClient(transport)
    gateway_coordinator = ZhonghongGatewayCoordinator(hass, entry, client)
    coordinator = ZhonghongCoordinator(hass, entry, client)
    await gateway_coordinator.async_config_entry_first_refresh()
    await coordinator.async_config_entry_first_refresh()

    gateway_identifier = f"gateway:{gateway_coordinator.data.device_id}"
    device_registry = dr.async_get(hass)

    def _register_gateway_device() -> dr.DeviceEntry:
        """Create or update the separately represented VRF gateway device."""
        info = gateway_coordinator.data
        return device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, gateway_identifier)},
            configuration_url=endpoint.configuration_url,
            manufacturer=MANUFACTURER,
            model=MODEL_GATEWAY,
            model_id=info.model_code,
            name=entry.title,
            serial_number=info.device_id,
            sw_version=info.software_version,
        )

    gateway_device = _register_gateway_device()

    @callback
    def async_update_gateway_device() -> None:
        """Refresh registry metadata after a gateway information update."""
        _register_gateway_device()

    entry.runtime_data = ZhonghongRuntimeData(
        client=client,
        coordinator=coordinator,
        gateway_coordinator=gateway_coordinator,
        gateway_identifier=gateway_identifier,
        gateway_device_id=gateway_device.id,
    )
    entry.async_on_unload(
        gateway_coordinator.async_add_listener(async_update_gateway_device)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    monitor_enabled = entry.options.get(
        CONF_REFRESH_ON_OTHER_CLIMATE_CHANGES,
        DEFAULT_REFRESH_ON_OTHER_CLIMATE_CHANGES,
    )
    LOGGER.debug(
        "Refresh listener for other climate entities enabled=%s", monitor_enabled
    )
    if monitor_enabled:
        async_setup_other_climate_listener(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZhonghongConfigEntry) -> bool:
    """Unload a Zhonghong HTTP config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
