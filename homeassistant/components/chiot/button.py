"""Button entities for the CHIOT integration."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from chiot_homeassistant_client import DeviceClient
from chiot_homeassistant_client.services import device_info, identify

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ChiotConfigEntry, ChiotCoordinator
from .entity import ChiotEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class ChiotButtonEntityDescription(ButtonEntityDescription):
    """Describe a CHIOT button."""

    service: str
    subtype: str
    press_fn: Callable[[DeviceClient], Awaitable[object]]


BUTTONS: tuple[ChiotButtonEntityDescription, ...] = (
    ChiotButtonEntityDescription(
        key="identify",
        service=identify.DESCRIPTOR.name,
        subtype="identify",
        translation_key="identify",
        device_class=ButtonDeviceClass.IDENTIFY,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda client: client.call_method(
            identify.DESCRIPTOR.name, "identify"
        ),
    ),
    ChiotButtonEntityDescription(
        key="reboot",
        service=device_info.DESCRIPTOR.name,
        subtype="reboot",
        translation_key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda client: client.call_method(
            device_info.DESCRIPTOR.name, "reboot"
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChiotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons for the services the device reports."""
    coordinator = entry.runtime_data
    services = coordinator.data.services if coordinator.data else frozenset()
    async_add_entities(
        ChiotButton(coordinator, description)
        for description in BUTTONS
        if description.service in services
    )


class ChiotButton(ChiotEntity, ButtonEntity):
    """A one-shot device action."""

    entity_description: ChiotButtonEntityDescription

    def __init__(
        self, coordinator: ChiotCoordinator, description: ChiotButtonEntityDescription
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, description.service, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Invoke the device method."""
        await self.coordinator.async_run(
            self.entity_description.press_fn, self.entity_description.service
        )
