"""Sensor tests for the CHIOT integration."""

from homeassistant.components.chiot.coordinator import ChiotState
from homeassistant.components.chiot.sensor import SENSORS, ChiotSensor
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfSignalStrength,
)


def test_sensor_descriptions() -> None:
    """Sensors expose raw units and let HA convert them for display."""
    descriptions = {description.key: description for description in SENSORS}
    assert descriptions["power"].native_unit_of_measurement == UnitOfPower.MILLIWATT
    assert descriptions["power"].suggested_unit_of_measurement == UnitOfPower.WATT
    assert (
        descriptions["voltage"].native_unit_of_measurement
        == UnitOfElectricPotential.MILLIVOLT
    )
    assert (
        descriptions["current"].native_unit_of_measurement
        == UnitOfElectricCurrent.MILLIAMPERE
    )
    assert (
        descriptions["power-consumption"].state_class
        is SensorStateClass.TOTAL_INCREASING
    )
    assert (
        descriptions["power-consumption"].native_unit_of_measurement
        == UnitOfEnergy.WATT_HOUR
    )
    assert descriptions["power-consumption"].translation_key == "energy_today"
    assert descriptions["power"].device_class is SensorDeviceClass.POWER
    assert descriptions["rated-power"].entity_category is EntityCategory.DIAGNOSTIC
    assert descriptions["rated-power"].entity_registry_enabled_default is False
    assert (
        descriptions["rssi"].native_unit_of_measurement
        == UnitOfSignalStrength.DECIBELS_MILLIWATT
    )
    assert descriptions["rssi"].entity_category is EntityCategory.DIAGNOSTIC


def test_sensor_values(coordinator) -> None:
    """Sensor values come from the cached service state."""
    by_key = {description.key: description for description in SENSORS}
    power = ChiotSensor(coordinator, by_key["power"])
    assert power.native_value == 0
    assert power.unique_id == f"{coordinator.device_id}_ElectricityCommon_power"

    description = by_key["power-consumption"]
    energy = ChiotSensor(coordinator, description)
    assert energy.unique_id.endswith("ElectricityCommon_power-consumption")


def test_sensor_missing_value(coordinator) -> None:
    """An entity whose value was not read reports unknown."""
    coordinator.data = ChiotState(
        info=coordinator.data.info,
        services=coordinator.data.services,
        states={"ElectricityCommon": {"power": 1200}},
    )
    by_key = {description.key: description for description in SENSORS}
    sensor = ChiotSensor(coordinator, by_key["voltage"])
    assert sensor.native_value is None
