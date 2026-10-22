"""Coordinator tests for the CHIOT integration."""

from unittest.mock import AsyncMock, MagicMock, patch

from chiot_homeassistant_client import (
    ChiotAuthError,
    ChiotConnectionError,
    ChiotSecurityError,
    Notification,
)
import pytest

from homeassistant.components.chiot.coordinator import ChiotCoordinator, ChiotState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from .common import (
    DEVICE_ID,
    DEVICE_INFO,
    INITIAL_STATE,
    SERVICES_ON_DEVICE,
    config_entry,
)
from .conftest import CHIOT


def _configure(client: AsyncMock) -> None:
    client.get_device_info.return_value = DEVICE_INFO
    client.get_services.return_value = list(SERVICES_ON_DEVICE)
    client.get_properties.side_effect = lambda name, *_: dict(
        INITIAL_STATE.get(name, {})
    )


async def _coordinator(hass: HomeAssistant, client: AsyncMock) -> ChiotCoordinator:
    entry = config_entry()
    entry.add_to_hass(hass)
    coordinator = ChiotCoordinator(hass, entry)
    coordinator._anchors = MagicMock()
    coordinator._client = client
    coordinator.data = None
    return coordinator


@pytest.mark.usefixtures("mock_chiot_client")
async def test_first_refresh(hass: HomeAssistant, mock_chiot_client: AsyncMock) -> None:
    """The first refresh reads DeviceInfo, Portfolio and every service."""
    _configure(mock_chiot_client)
    coordinator = await _coordinator(hass, mock_chiot_client)
    data = await coordinator._async_update_data()
    assert data.info.device_id == DEVICE_ID
    assert data.services == frozenset(SERVICES_ON_DEVICE)
    assert data.states["SmartSocketCommon"] == {"power-switch_1": 0}
    assert data.states["ElectricityCommon"]["voltage"] == 230000


@pytest.mark.usefixtures("mock_chiot_client")
async def test_auth_failure(hass: HomeAssistant, mock_chiot_client: AsyncMock) -> None:
    """A rejected token becomes ConfigEntryAuthFailed."""
    _configure(mock_chiot_client)
    mock_chiot_client.get_device_info.side_effect = ChiotAuthError("bad")
    coordinator = await _coordinator(hass, mock_chiot_client)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.usefixtures("mock_chiot_client")
async def test_connection_failure(
    hass: HomeAssistant, mock_chiot_client: AsyncMock
) -> None:
    """Connection failures become UpdateFailed."""
    _configure(mock_chiot_client)
    mock_chiot_client.get_device_info.side_effect = ChiotConnectionError("refused")
    coordinator = await _coordinator(hass, mock_chiot_client)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.usefixtures("mock_chiot_client")
async def test_identity_mismatch(
    hass: HomeAssistant, mock_chiot_client: AsyncMock
) -> None:
    """A different DeviceInfo deviceId is a security failure, not retryable."""
    _configure(mock_chiot_client)
    mock_chiot_client.get_device_info.return_value = DEVICE_INFO.__class__(
        device_id="other",
        manufacturer="CHIOT",
        model="HPL-JA21",
        hardware_version="1.0",
        software_version="2.0.1",
    )
    coordinator = await _coordinator(hass, mock_chiot_client)
    with pytest.raises(ChiotSecurityError):
        await coordinator._async_update_data()


@pytest.mark.usefixtures("mock_chiot_client")
async def test_notification_merge(
    hass: HomeAssistant, mock_chiot_client: AsyncMock
) -> None:
    """PropertyNotify values are merged into the cached state."""
    _configure(mock_chiot_client)
    coordinator = await _coordinator(hass, mock_chiot_client)
    coordinator.data = ChiotState(
        info=DEVICE_INFO,
        services=frozenset(SERVICES_ON_DEVICE),
        states={
            **INITIAL_STATE,
            "ElectricityCommon": dict(INITIAL_STATE["ElectricityCommon"]),
        },
    )
    notifications: list[ChiotState] = []
    coordinator.async_add_listener(lambda: notifications.append(coordinator.data))
    coordinator._handle_notification(
        Notification(
            device_id=DEVICE_ID,
            request_id="push",
            services=(
                {
                    "serviceName": "ElectricityCommon",
                    "type": "propertyNotify",
                    "params": {"power": 1500},
                },
            ),
        )
    )
    assert coordinator.data.states["ElectricityCommon"]["power"] == 1500
    assert coordinator.data.states["ElectricityCommon"]["voltage"] == 230000
    assert notifications


@pytest.mark.usefixtures("mock_chiot_client")
async def test_notification_ignored(
    hass: HomeAssistant, mock_chiot_client: AsyncMock
) -> None:
    """Unknown services, unknown properties and invalid values are ignored."""
    _configure(mock_chiot_client)
    coordinator = await _coordinator(hass, mock_chiot_client)
    coordinator.data = ChiotState(
        info=DEVICE_INFO,
        services=frozenset(SERVICES_ON_DEVICE),
        states={"SmartSocketCommon": {"power-switch_1": 0}},
    )
    for service in (
        {"serviceName": "Unknown", "type": "propertyNotify", "params": {"a": 1}},
        {
            "serviceName": "SmartSocketCommon",
            "type": "propertyNotify",
            "params": {"unknown": 1},
        },
        {
            "serviceName": "ElectricityCommon",
            "type": "propertyNotify",
            "params": {"power": -1},
        },
        {"serviceName": "SmartSocketCommon", "type": "event", "params": {}},
    ):
        coordinator._handle_notification(
            Notification(device_id=DEVICE_ID, request_id="push", services=(service,))
        )
    assert coordinator.data.states["SmartSocketCommon"] == {"power-switch_1": 0}
    assert "Unknown" not in coordinator.data.states


@pytest.mark.usefixtures("mock_chiot_client")
async def test_disconnect_marks_unavailable(
    hass: HomeAssistant, mock_chiot_client: AsyncMock
) -> None:
    """A disconnect marks the coordinator failed and schedules a reconnect."""
    _configure(mock_chiot_client)
    coordinator = await _coordinator(hass, mock_chiot_client)
    coordinator.data = ChiotState(
        info=DEVICE_INFO, services=frozenset(SERVICES_ON_DEVICE)
    )
    coordinator.last_update_success = True
    with patch.object(
        ChiotCoordinator, "_async_reconnect", new=AsyncMock()
    ) as reconnect:
        coordinator._handle_disconnect("gone")
        await hass.async_block_till_done()
    assert coordinator.last_update_success is False
    reconnect.assert_awaited()


@pytest.mark.usefixtures("mock_chiot_client")
async def test_reconnect_auth_failure(
    hass: HomeAssistant, mock_chiot_client: AsyncMock
) -> None:
    """A rejected token during reconnect starts reauth and stops retrying."""
    _configure(mock_chiot_client)
    coordinator = await _coordinator(hass, mock_chiot_client)
    coordinator.config_entry.async_start_reauth = MagicMock()  # type: ignore[method-assign]
    mock_chiot_client.connect.side_effect = ChiotAuthError("bad")
    with patch(f"{CHIOT}.coordinator.RECONNECT_DELAYS", (0,)):
        await coordinator._async_reconnect()
    coordinator.config_entry.async_start_reauth.assert_called_once()  # type: ignore[attr-defined]


@pytest.mark.usefixtures("mock_chiot_client")
async def test_run_command_errors(
    hass: HomeAssistant, mock_chiot_client: AsyncMock
) -> None:
    """Client errors are translated to HomeAssistantError."""
    _configure(mock_chiot_client)
    coordinator = await _coordinator(hass, mock_chiot_client)
    coordinator.data = ChiotState(
        info=DEVICE_INFO, services=frozenset(SERVICES_ON_DEVICE)
    )

    async def _fail(_client: object) -> None:
        raise ChiotConnectionError("gone")

    with pytest.raises(HomeAssistantError):
        await coordinator.async_run(_fail, "SmartSocketCommon")


@pytest.mark.usefixtures("mock_chiot_client")
async def test_run_command_refreshes_service(
    hass: HomeAssistant, mock_chiot_client: AsyncMock
) -> None:
    """A successful command re-reads only its own service."""
    _configure(mock_chiot_client)
    coordinator = await _coordinator(hass, mock_chiot_client)
    coordinator.data = ChiotState(
        info=DEVICE_INFO, services=frozenset(SERVICES_ON_DEVICE), states={}
    )
    mock_chiot_client.services = dict.fromkeys(SERVICES_ON_DEVICE)
    calls: list[object] = []

    async def _command(client: object) -> None:
        calls.append(client)

    await coordinator.async_run(_command, "SmartSocketCommon")
    assert calls
    mock_chiot_client.get_properties.assert_awaited_with("SmartSocketCommon")
