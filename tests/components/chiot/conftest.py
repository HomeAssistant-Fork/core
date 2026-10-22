"""Fixtures for the CHIOT integration tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.chiot.coordinator import ChiotCoordinator, ChiotState

from .common import (
    DEVICE_ID,
    DEVICE_INFO,
    INITIAL_STATE,
    SERVICES_ON_DEVICE,
    config_entry,
)

CHIOT = "homeassistant.components.chiot"


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Prevent the integration from setting up platforms."""
    with patch(f"{CHIOT}.async_setup_entry", return_value=True) as setup:
        yield setup


@pytest.fixture
def mock_ble_client() -> AsyncMock:
    """Return a ProvisioningClient mock whose handshake succeeds."""
    client = AsyncMock()
    client.connect_and_verify.return_value = MagicMock(
        device_id=DEVICE_ID, model="HPL-JA21"
    )
    return client


@pytest.fixture
def mock_chiot_client() -> Generator[MagicMock]:
    """Patch DeviceClient used by the coordinator."""
    client = MagicMock()
    client.closed = False
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.get_device_info = AsyncMock(return_value=DEVICE_INFO)
    client.get_services = AsyncMock(return_value=list(SERVICES_ON_DEVICE))
    client.get_properties = AsyncMock(
        side_effect=lambda name, *_: dict(INITIAL_STATE.get(name, {}))
    )
    client.set_properties = AsyncMock(return_value={})
    client.call_method = AsyncMock(return_value={})
    client.set_notification_callback = MagicMock()
    client.set_disconnect_callback = MagicMock()
    client.services = dict.fromkeys(SERVICES_ON_DEVICE)
    with patch(f"{CHIOT}.coordinator.DeviceClient", return_value=client):
        yield client


@pytest.fixture
def mock_trust_anchors() -> Generator[None]:
    """Avoid reading package data inside the event loop."""
    with patch(f"{CHIOT}.async_get_trust_anchors", return_value=MagicMock()):
        yield


@pytest.fixture
def coordinator(hass) -> ChiotCoordinator:
    """Return a coordinator with a populated state cache."""
    entry = config_entry()
    entry.add_to_hass(hass)
    coordinator = ChiotCoordinator(hass, entry)
    coordinator.data = ChiotState(
        info=DEVICE_INFO,
        services=frozenset(SERVICES_ON_DEVICE),
        states={name: dict(values) for name, values in INITIAL_STATE.items()},
    )
    coordinator.last_update_success = True
    return coordinator
