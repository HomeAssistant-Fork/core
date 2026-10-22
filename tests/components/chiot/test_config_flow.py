"""Config flow tests for the CHIOT integration."""

from dataclasses import replace
from ipaddress import IPv4Address
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from chiot_homeassistant_client import (
    ChiotAuthError,
    ChiotConnectionError,
    ChiotProtocolError,
    ChiotProvisionError,
    ChiotSecurityError,
    ChiotTimeoutError,
    ChiotUnsupportedVersion,
)
import pytest

from homeassistant import config_entries
from homeassistant.components.chiot.const import (
    CONF_DEVICE_ID,
    CONF_MODEL,
    CONF_MODEL_ID,
    CONF_PASSWORD,
    CONF_SSID,
    CONF_TOKEN,
    DOMAIN,
)
from homeassistant.const import CONF_ADDRESS, CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .common import ADDRESS, DEVICE_ID
from .discovery import service_info

from tests.common import MockConfigEntry

CHIOT = "homeassistant.components.chiot"
DISCOVERY_INFO = service_info("CIHA-1001-1-a1b2", ADDRESS)
NON_PROVISIONING_INFO = service_info("CIHA-1001-0-a1b2", "AA:BB:CC:DD:EE:00")

WSS_PORT = 8443
TXT = {
    "txtvers": "1",
    "id": DEVICE_ID,
    "mfg": "CHIOT",
    "model": "HPL-JA21",
    "model_id": "1001",
    "hw": "1.0",
    "fw": "2.0.1",
    "path": "/chiot/v1/ws",
    "proto": "chiot.local.v1",
}


def zeroconf_info(**changes: Any) -> ZeroconfServiceInfo:
    """Build a zeroconf discovery object."""
    values: dict[str, Any] = {
        "ip_address": IPv4Address("192.168.1.50"),
        "ip_addresses": [IPv4Address("192.168.1.50")],
        "port": WSS_PORT,
        "hostname": "chiot-a1b2.local.",
        "type": "_chiot-wss._tcp.local.",
        "name": "chiot-a1b2._chiot-wss._tcp.local.",
        "properties": dict(TXT),
    }
    values.update(changes)
    return ZeroconfServiceInfo(**values)


@pytest.fixture(autouse=True)
def mock_scanners() -> Any:
    """Pretend a connectable adapter is present."""
    with patch(f"{CHIOT}.config_flow.bluetooth.async_scanner_count", return_value=1):
        yield


@pytest.fixture(autouse=True)
def mock_ble_device() -> Any:
    """Return a BLEDevice for the discovery address."""
    with patch(
        f"{CHIOT}.config_flow.bluetooth.async_ble_device_from_address",
        return_value=MagicMock(),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_trust_anchors() -> Any:
    """Avoid reading package data inside the event loop."""
    with patch(f"{CHIOT}.config_flow.load_trust_anchors", return_value=MagicMock()):
        yield


async def _advance(hass, flow_id: str, data: dict[str, Any] | None = None) -> dict:
    """Advance a flow and drain progress steps until input is required."""
    result = await hass.config_entries.flow.async_configure(flow_id, data)
    while result["type"] is FlowResultType.SHOW_PROGRESS:
        result = await hass.config_entries.flow.async_configure(flow_id)
    return result


async def _start_bluetooth(hass) -> dict:
    """Start from Bluetooth discovery and confirm it."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=DISCOVERY_INFO,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    return await _advance(hass, result["flow_id"], {})


def _ble_client(device_id: str = DEVICE_ID) -> AsyncMock:
    client = AsyncMock()
    client.connect_and_verify.return_value = MagicMock(
        device_id=device_id, model="HPL-JA21"
    )
    return client


def _online() -> tuple[Any, Any]:
    """Patch mDNS discovery and the WSS probe to succeed."""
    return (
        patch(
            f"{CHIOT}.config_flow._find_device", return_value=("127.0.0.1", WSS_PORT)
        ),
        patch(f"{CHIOT}.config_flow._probe_wss", return_value="HPL-JA21"),
    )


def _wifi() -> dict[str, str]:
    return {CONF_SSID: "MyWiFi", CONF_PASSWORD: "secret"}


async def test_bluetooth_success(hass) -> None:
    """The Bluetooth flow verifies, provisions and creates a clean entry."""
    client = _ble_client()
    find, probe = _online()
    with (
        patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client),
        find,
        probe,
    ):
        result = await _start_bluetooth(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "wifi"
        result = await _advance(hass, result["flow_id"], _wifi())

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "HPL-JA21"
    data = result["data"]
    assert data[CONF_DEVICE_ID] == DEVICE_ID
    assert data[CONF_HOST] == "127.0.0.1"
    assert data[CONF_PORT] == WSS_PORT
    assert data[CONF_MODEL_ID] == 1001
    assert len(data[CONF_TOKEN]) >= 40
    assert CONF_PASSWORD not in data
    assert CONF_SSID not in data
    assert result["result"].unique_id == DEVICE_ID
    client.provision.assert_awaited_once()
    assert client.provision.await_args.args[1] == "secret"


async def test_no_connectable_adapter(hass) -> None:
    """Without a connectable scanner the flow aborts."""
    with patch(f"{CHIOT}.config_flow.bluetooth.async_scanner_count", return_value=0):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_connectable_adapter"


async def test_not_provisionable(hass) -> None:
    """Devices outside provisioning mode are ignored."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=NON_PROVISIONING_INFO,
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_provisionable"


async def test_invalid_advertisement(hass) -> None:
    """Malformed advertisement names abort the flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=replace(NON_PROVISIONING_INFO, name="garbage"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_info"


async def test_user_lists_provisionable_devices(hass) -> None:
    """Only provisioning-mode devices are offered."""
    with patch(
        f"{CHIOT}.config_flow.bluetooth.async_discovered_service_info",
        return_value=[DISCOVERY_INFO, NON_PROVISIONING_INFO],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert ADDRESS in str(result["data_schema"].schema)


async def test_user_select_device(hass) -> None:
    """Selecting a device continues to verification."""
    client = _ble_client()
    with (
        patch(
            f"{CHIOT}.config_flow.bluetooth.async_discovered_service_info",
            return_value=[DISCOVERY_INFO],
        ),
        patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await _advance(hass, result["flow_id"], {CONF_ADDRESS: ADDRESS})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "wifi"


async def test_user_no_devices(hass) -> None:
    """An empty scan aborts."""
    with patch(
        f"{CHIOT}.config_flow.bluetooth.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


@pytest.mark.parametrize(
    ("exception", "reason"),
    [
        (ChiotSecurityError("bad"), "untrusted_device"),
        (ChiotUnsupportedVersion("bad"), "unsupported_version"),
        (ChiotTimeoutError("timeout"), "cannot_connect"),
        (ChiotConnectionError("MTU is below"), "mtu_too_small"),
        (ChiotConnectionError("refused"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
    ids=["security", "version", "timeout", "mtu", "connection", "unexpected"],
)
async def test_verify_errors(hass, exception: Exception, reason: str) -> None:
    """Verification errors map to abort reasons."""
    client = AsyncMock()
    client.connect_and_verify.side_effect = exception
    with patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client):
        result = await _start_bluetooth(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


async def test_device_not_found(hass) -> None:
    """A missing BLEDevice aborts."""
    with patch(
        f"{CHIOT}.config_flow.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        result = await _start_bluetooth(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_not_found"


@pytest.mark.parametrize(
    ("error_code", "reason", "form_error"),
    [
        (1, None, "invalid_wifi"),
        (2, "device_save_failed", None),
        (255, "device_save_failed", None),
    ],
    ids=["invalid-wifi", "save-failed", "internal"],
)
async def test_provision_errors(
    hass, error_code: int, reason: str | None, form_error: str | None
) -> None:
    """Provisioning errors map to a form error or an abort."""
    client = _ble_client()
    client.provision.side_effect = ChiotProvisionError("bad", error_code=error_code)
    with patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client):
        result = await _start_bluetooth(hass)
        result = await _advance(hass, result["flow_id"], _wifi())
    if form_error is not None:
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "wifi"
        assert result["errors"] == {"base": form_error}
    else:
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == reason


async def test_provision_security_error(hass) -> None:
    """A security failure in the second session aborts without retrying."""
    client = _ble_client()
    client.provision.side_effect = ChiotSecurityError("bad")
    with patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client):
        result = await _start_bluetooth(hass)
        result = await _advance(hass, result["flow_id"], _wifi())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "untrusted_device"


async def test_device_changed(hass) -> None:
    """A different device_id in the second session aborts."""
    with patch(
        f"{CHIOT}.config_flow._provisioning_client",
        side_effect=[_ble_client(), _ble_client("other-device")],
    ):
        result = await _start_bluetooth(hass)
        result = await _advance(hass, result["flow_id"], _wifi())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_changed"


async def test_result_unknown_continues(hass) -> None:
    """An unknown provisioning result still waits for the device."""
    client = _ble_client()
    client.provision.side_effect = ChiotProvisionError(
        "disconnected", result_unknown=True
    )
    find, probe = _online()
    with (
        patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client),
        find,
        probe,
    ):
        result = await _start_bluetooth(hass)
        result = await _advance(hass, result["flow_id"], _wifi())
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_not_online(hass) -> None:
    """A device that never appears aborts with not_online."""
    client = _ble_client()
    with (
        patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client),
        patch(f"{CHIOT}.config_flow._find_device", side_effect=TimeoutError),
    ):
        result = await _start_bluetooth(hass)
        result = await _advance(hass, result["flow_id"], _wifi())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_online"


async def test_probe_unreachable(hass) -> None:
    """A WSS connection failure while waiting aborts with not_online."""
    client = _ble_client()
    with (
        patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client),
        patch(
            f"{CHIOT}.config_flow._find_device",
            return_value=("127.0.0.1", WSS_PORT),
        ),
        patch(
            f"{CHIOT}.config_flow._probe_wss",
            side_effect=ChiotConnectionError("refused"),
        ),
    ):
        result = await _start_bluetooth(hass)
        result = await _advance(hass, result["flow_id"], _wifi())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_online"


async def test_auth_failed_on_online(hass) -> None:
    """A rejected token aborts with auth_failed."""
    client = _ble_client()
    with (
        patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client),
        patch(
            f"{CHIOT}.config_flow._find_device",
            return_value=("127.0.0.1", WSS_PORT),
        ),
        patch(f"{CHIOT}.config_flow._probe_wss", side_effect=ChiotAuthError("bad")),
    ):
        result = await _start_bluetooth(hass)
        result = await _advance(hass, result["flow_id"], _wifi())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "auth_failed"


async def test_device_changed_after_online(hass) -> None:
    """A changed DeviceInfo deviceId aborts with device_changed."""
    client = _ble_client()
    with (
        patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client),
        patch(
            f"{CHIOT}.config_flow._find_device",
            return_value=("127.0.0.1", WSS_PORT),
        ),
        patch(
            f"{CHIOT}.config_flow._probe_wss",
            side_effect=ChiotProtocolError("changed"),
        ),
    ):
        result = await _start_bluetooth(hass)
        result = await _advance(hass, result["flow_id"], _wifi())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unknown"


async def test_reprovision_updates_entry(hass) -> None:
    """An already configured device confirms and updates its entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={
            CONF_DEVICE_ID: DEVICE_ID,
            CONF_HOST: "192.168.1.10",
            CONF_PORT: WSS_PORT,
            CONF_TOKEN: "old-token",
            CONF_MODEL: "HPL-JA21",
            CONF_MODEL_ID: 1001,
        },
    )
    entry.add_to_hass(hass)
    client = _ble_client()
    with (
        patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client),
        patch.object(type(hass.config_entries), "async_schedule_reload"),
        patch(
            f"{CHIOT}.config_flow._find_device",
            return_value=("127.0.0.1", WSS_PORT),
        ),
        patch(f"{CHIOT}.config_flow._probe_wss", return_value="HPL-JA21"),
    ):
        result = await _start_bluetooth(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reprovision_confirm"
        result = await _advance(hass, result["flow_id"], {"confirm": True})
        assert result["step_id"] == "wifi"
        result = await _advance(hass, result["flow_id"], _wifi())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reprovision_successful"
    assert entry.data[CONF_TOKEN] != "old-token"
    assert entry.data[CONF_HOST] == "127.0.0.1"
    assert entry.data[CONF_PORT] == WSS_PORT


async def test_reprovision_declined(hass) -> None:
    """Declining re-provisioning aborts as already configured."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DEVICE_ID, data={CONF_TOKEN: "old"}
    )
    entry.add_to_hass(hass)
    client = _ble_client()
    with patch(f"{CHIOT}.config_flow._provisioning_client", return_value=client):
        result = await _start_bluetooth(hass)
        result = await _advance(hass, result["flow_id"], {"confirm": False})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_updates_host(hass) -> None:
    """A configured device with a new address is updated and reloaded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={CONF_HOST: "192.168.1.10", CONF_PORT: WSS_PORT},
    )
    entry.add_to_hass(hass)
    with patch.object(type(hass.config_entries), "async_schedule_reload") as reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=zeroconf_info(),
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.50"
    assert entry.data[CONF_PORT] == WSS_PORT
    assert reload.called


async def test_zeroconf_unchanged_address(hass) -> None:
    """An unchanged address does not reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={CONF_HOST: "192.168.1.50", CONF_PORT: WSS_PORT},
    )
    entry.add_to_hass(hass)
    with patch.object(type(hass.config_entries), "async_schedule_reload") as reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=zeroconf_info(),
        )
    assert result["reason"] == "already_configured"
    assert not reload.called


async def test_zeroconf_requires_provisioning(hass) -> None:
    """An unconfigured device cannot be added from mDNS alone."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf_info(),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "provision_required"


async def test_zeroconf_invalid_txt(hass) -> None:
    """Invalid TXT records are rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf_info(properties={**TXT, "path": "/other"}),
    )
    assert result["reason"] == "invalid_discovery_info"


async def test_zeroconf_ipv6_only(hass) -> None:
    """IPv6-only discoveries are ignored."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf_info(ip_addresses=[]),
    )
    assert result["reason"] == "invalid_discovery_info"


async def test_zeroconf_update_flow_aborts_in_progress(hass) -> None:
    """A zeroconf flow for an unconfigured device is aborted as provision_required."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=zeroconf_info(),
    )
    assert result["flow_id"] not in hass.config_entries.flow.async_progress()
