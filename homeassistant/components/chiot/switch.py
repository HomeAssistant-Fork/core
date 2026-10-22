"""Switch entities for the CHIOT integration."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from chiot_homeassistant_client import DeviceClient
from chiot_homeassistant_client.services import (
    do_not_disturb,
    electricity,
    poweroff_memory,
    smart_socket,
)

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ChiotConfigEntry, ChiotCoordinator
from .entity import ChiotEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class ChiotSwitchEntityDescription(SwitchEntityDescription):
    """Describe a CHIOT switch."""

    service: str
    is_on_fn: Callable[[dict[str, Any]], bool]
    set_fn: Callable[[DeviceClient, bool], Awaitable[Any]]


SWITCHES: tuple[ChiotSwitchEntityDescription, ...] = (
    ChiotSwitchEntityDescription(
        key=smart_socket.POWER_SWITCH,
        service=smart_socket.DESCRIPTOR.name,
        device_class=SwitchDeviceClass.OUTLET,
        is_on_fn=lambda state: state.get(smart_socket.POWER_SWITCH) == 1,
        set_fn=lambda client, on: client.set_properties(
            smart_socket.DESCRIPTOR.name, smart_socket.set_power(on)
        ),
    ),
    ChiotSwitchEntityDescription(
        key=do_not_disturb.PROPERTY,
        service=do_not_disturb.DESCRIPTOR.name,
        translation_key="do_not_disturb",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda state: state.get(do_not_disturb.PROPERTY) == 1,
        set_fn=lambda client, on: client.set_properties(
            do_not_disturb.DESCRIPTOR.name, do_not_disturb.set_enabled(on)
        ),
    ),
    ChiotSwitchEntityDescription(
        key=poweroff_memory.PROPERTY,
        service=poweroff_memory.DESCRIPTOR.name,
        translation_key="power_outage_memory",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda state: state.get(poweroff_memory.PROPERTY) == 1,
        set_fn=lambda client, on: client.set_properties(
            poweroff_memory.DESCRIPTOR.name, poweroff_memory.set_enabled(on)
        ),
    ),
    ChiotSwitchEntityDescription(
        key=electricity.POWER_ALARM_ENABLE,
        service=electricity.DESCRIPTOR.name,
        translation_key="overload_alarm",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda state: state.get(electricity.POWER_ALARM_ENABLE) == 1,
        set_fn=lambda client, on: client.set_properties(
            electricity.DESCRIPTOR.name, {electricity.POWER_ALARM_ENABLE: int(on)}
        ),
    ),
    ChiotSwitchEntityDescription(
        key=electricity.CONSUMPTION_ALARM_ENABLE,
        service=electricity.DESCRIPTOR.name,
        translation_key="daily_consumption_alarm",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda state: state.get(electricity.CONSUMPTION_ALARM_ENABLE) == 1,
        set_fn=lambda client, on: client.set_properties(
            electricity.DESCRIPTOR.name, {electricity.CONSUMPTION_ALARM_ENABLE: int(on)}
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChiotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switch entities for the services the device reports."""
    coordinator = entry.runtime_data
    services = coordinator.data.services if coordinator.data else frozenset()
    async_add_entities(
        ChiotSwitch(coordinator, description)
        for description in SWITCHES
        if description.service in services
    )


class ChiotSwitch(ChiotEntity, SwitchEntity):
    """A switch backed by one device property."""

    entity_description: ChiotSwitchEntityDescription

    def __init__(
        self, coordinator: ChiotCoordinator, description: ChiotSwitchEntityDescription
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, description.service, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return the cached state."""
        return self.entity_description.is_on_fn(self.service_state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.coordinator.async_run(
            lambda client: self.entity_description.set_fn(client, True),
            self.entity_description.service,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.coordinator.async_run(
            lambda client: self.entity_description.set_fn(client, False),
            self.entity_description.service,
        )
