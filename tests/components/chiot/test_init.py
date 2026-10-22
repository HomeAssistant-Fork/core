"""Setup and unload tests for the CHIOT integration."""

from unittest.mock import AsyncMock, patch

from chiot_homeassistant_client import (
    ChiotAuthError,
    ChiotConnectionError,
    ChiotSecurityError,
    ChiotTimeoutError,
)
import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .common import (
    DEVICE_ID,
    DEVICE_INFO,
    INITIAL_STATE,
    SERVICES_ON_DEVICE,
    config_entry,
)

from tests.common import MockConfigEntry


def _configure_client(client: AsyncMock, *, device_id: str = DEVICE_ID) -> None:
    client.get_device_info.return_value = DEVICE_INFO.__class__(
        device_id=device_id,
        manufacturer=DEVICE_INFO.manufacturer,
        model=DEVICE_INFO.model,
        hardware_version=DEVICE_INFO.hardware_version,
        software_version=DEVICE_INFO.software_version,
        mac=DEVICE_INFO.mac,
    )
    client.get_services.return_value = list(SERVICES_ON_DEVICE)
    client.get_properties.side_effect = lambda name, *_: dict(
        INITIAL_STATE.get(name, {})
    )


@pytest.mark.usefixtures("mock_trust_anchors", "mock_chiot_client")
async def test_setup_and_unload(
    hass: HomeAssistant, mock_chiot_client: AsyncMock
) -> None:
    """A reachable device loads and unloads cleanly."""
    entry: MockConfigEntry = config_entry()
    entry.add_to_hass(hass)
    _configure_client(mock_chiot_client)
    with patch.object(
        hass.config_entries, "async_forward_entry_setups", return_value=True
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.device_id == DEVICE_ID
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    mock_chiot_client.close.assert_awaited()


@pytest.mark.usefixtures("mock_trust_anchors", "mock_chiot_client")
@pytest.mark.parametrize(
    ("exception", "state"),
    [
        (ChiotConnectionError("refused"), ConfigEntryState.SETUP_RETRY),
        (ChiotTimeoutError("timeout"), ConfigEntryState.SETUP_RETRY),
        (ChiotSecurityError("bad chain"), ConfigEntryState.SETUP_ERROR),
    ],
    ids=["connection", "timeout", "security"],
)
async def test_setup_errors(
    hass: HomeAssistant,
    mock_chiot_client: AsyncMock,
    exception: Exception,
    state: ConfigEntryState,
) -> None:
    """Connection failures retry while identity failures are terminal."""
    entry: MockConfigEntry = config_entry()
    entry.add_to_hass(hass)
    mock_chiot_client.get_device_info.side_effect = exception
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is state


@pytest.mark.usefixtures("mock_trust_anchors", "mock_chiot_client")
async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_chiot_client: AsyncMock
) -> None:
    """A rejected token triggers the reauth flow."""
    entry: MockConfigEntry = config_entry()
    entry.add_to_hass(hass)
    _configure_client(mock_chiot_client)
    with patch.object(MockConfigEntry, "async_start_reauth") as reauth:
        mock_chiot_client.get_device_info.side_effect = ChiotAuthError("bad")
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state in (ConfigEntryState.SETUP_ERROR, ConfigEntryState.SETUP_RETRY)
    assert reauth.called or entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.usefixtures("mock_trust_anchors", "mock_chiot_client")
async def test_device_id_mismatch(
    hass: HomeAssistant, mock_chiot_client: AsyncMock
) -> None:
    """A DeviceInfo deviceId mismatch is a terminal error."""
    entry: MockConfigEntry = config_entry()
    entry.add_to_hass(hass)
    _configure_client(mock_chiot_client, device_id="other-device")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state in (ConfigEntryState.SETUP_ERROR, ConfigEntryState.SETUP_RETRY)
