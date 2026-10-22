"""Bluetooth discovery fixtures for the CHIOT tests."""

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from tests.components.bluetooth import generate_advertisement_data, generate_ble_device

SERVICE_UUID = "6ca1a426-d2db-4403-85fc-aa9bec54e6a1"


def service_info(name: str, address: str) -> BluetoothServiceInfoBleak:
    """Return a connectable advertisement for a CHIOT device."""
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_uuids=[SERVICE_UUID],
        service_data={},
        source="local",
        device=generate_ble_device(address=address, name=name),
        advertisement=generate_advertisement_data(
            local_name=name, service_uuids=[SERVICE_UUID]
        ),
        time=0,
        connectable=True,
        tx_power=-127,
    )
