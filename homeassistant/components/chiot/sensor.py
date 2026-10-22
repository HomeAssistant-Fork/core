"""Sensor entities for the CHIOT integration."""

from dataclasses import dataclass
from typing import Any

from chiot_homeassistant_client.services import electricity, wifi

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfSignalStrength,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ChiotConfigEntry, ChiotCoordinator
from .entity import ChiotEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ChiotSensorEntityDescription(SensorEntityDescription):
    """Describe a CHIOT sensor."""

    service: str


SENSORS: tuple[ChiotSensorEntityDescription, ...] = (
    ChiotSensorEntityDescription(
        key=electricity.POWER,
        service=electricity.DESCRIPTOR.name,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.MILLIWATT,
        suggested_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ChiotSensorEntityDescription(
        key=electricity.VOLTAGE,
        service=electricity.DESCRIPTOR.name,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        suggested_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ChiotSensorEntityDescription(
        key=electricity.CURRENT,
        service=electricity.DESCRIPTOR.name,
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.MILLIAMPERE,
        suggested_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ChiotSensorEntityDescription(
        key=electricity.POWER_CONSUMPTION,
        service=electricity.DESCRIPTOR.name,
        translation_key="energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    ChiotSensorEntityDescription(
        key=electricity.RATED_POWER,
        service=electricity.DESCRIPTOR.name,
        translation_key="rated_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    ChiotSensorEntityDescription(
        key=wifi.RSSI,
        service=wifi.DESCRIPTOR.name,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=UnitOfSignalStrength.DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ChiotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for the services the device reports."""
    coordinator = entry.runtime_data
    services = coordinator.data.services if coordinator.data else frozenset()
    async_add_entities(
        ChiotSensor(coordinator, description)
        for description in SENSORS
        if description.service in services
    )


class ChiotSensor(ChiotEntity, SensorEntity):
    """A read-only numeric sensor."""

    entity_description: ChiotSensorEntityDescription

    def __init__(
        self, coordinator: ChiotCoordinator, description: ChiotSensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.service, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the cached raw value."""
        return self.service_state.get(self.entity_description.key)
