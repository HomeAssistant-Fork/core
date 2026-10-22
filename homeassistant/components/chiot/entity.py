"""Shared entity behavior for the CHIOT integration."""

from typing import Any

from chiot_homeassistant_client import DeviceInfo

from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo as DeviceInfoRegistry,
    format_mac,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MODEL_ID, DOMAIN
from .coordinator import ChiotCoordinator


class ChiotEntity(CoordinatorEntity[ChiotCoordinator]):
    """Base entity bound to the device state cache."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ChiotCoordinator,
        service: str,
        key: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._service = service
        self._attr_unique_id = f"{coordinator.device_id}_{service}_{key}"
        self._attr_device_info = _device_info(coordinator)

    @property
    def available(self) -> bool:
        """Return True when the coordinator and the service are usable."""
        return (
            super().available
            and self.coordinator.data is not None
            and self._service in self.coordinator.data.services
            and self._service not in self.coordinator.data.unavailable
        )

    @property
    def service_state(self) -> dict[str, Any]:
        """Return the cached properties of this entity's service."""
        if self.coordinator.data is None:
            return {}
        return self.coordinator.data.states.get(self._service, {})


def _device_info(coordinator: ChiotCoordinator) -> DeviceInfoRegistry:
    info: DeviceInfo | None = coordinator.data.info if coordinator.data else None
    entry = coordinator.config_entry
    device_info = DeviceInfoRegistry(
        identifiers={(DOMAIN, coordinator.device_id)},
        manufacturer=entry.data.get("manufacturer")
        or (info.manufacturer if info else None),
        model=info.model if info else entry.data.get("model"),
        model_id=str(entry.data[CONF_MODEL_ID])
        if entry.data.get(CONF_MODEL_ID)
        else None,
        sw_version=info.software_version if info else None,
        hw_version=info.hardware_version if info else None,
    )
    if info is not None and info.mac:
        device_info["connections"] = {(CONNECTION_NETWORK_MAC, format_mac(info.mac))}
    return device_info
