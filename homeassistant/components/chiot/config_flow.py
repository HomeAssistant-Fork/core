"""Config flow for the CHIOT integration."""

import asyncio
from ipaddress import IPv4Address
import logging
import secrets
from typing import Any, override

from chiot_homeassistant_client import (
    SERVICE_TYPE,
    ChiotAuthError,
    ChiotConnectionError,
    ChiotProtocolError,
    ChiotProvisionError,
    ChiotSecurityError,
    ChiotTimeoutError,
    ChiotUnsupportedVersion,
    DeviceClient,
    ProvisioningClient,
    VerifiedDevice,
    is_provisioning_name,
    load_trust_anchors,
    parse_advertisement_name,
    parse_txt,
)
import probatio
from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser

from homeassistant.components import bluetooth
from homeassistant.components.zeroconf import async_get_async_instance
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_HOST, CONF_MODEL, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    BLE_PROVISION_TIMEOUT,
    BLE_VERIFY_TIMEOUT,
    CONF_DEVICE_ID,
    CONF_MODEL_ID,
    CONF_PASSWORD,
    CONF_SSID,
    CONF_TOKEN,
    DOMAIN,
    WAIT_ONLINE_TIMEOUT,
    WSS_PORT,
)

_LOGGER = logging.getLogger(__name__)


class _DeviceChangedError(ChiotProtocolError):
    """The device reported a different identity than the verified one."""


async def _probe_wss(
    hass: HomeAssistant,
    host: str,
    port: int,
    device_id: str,
    token: str,
    anchors: Any,
) -> str:
    """Connect with the token and return the reported model; raises on failure."""
    client = _device_client(
        async_get_clientsession(hass), host, port, device_id, token, anchors
    )
    try:
        async with asyncio.timeout(BLE_PROVISION_TIMEOUT):
            await client.connect()
            info = await client.get_device_info()
    finally:
        await client.close()
    if info.device_id != device_id:
        raise _DeviceChangedError("DeviceInfo deviceId changed after provisioning")
    return info.model


def _service_browser(aiozc: Any, handler: Any) -> AsyncServiceBrowser:
    """Return a service browser; kept separate so tests can patch it."""
    return AsyncServiceBrowser(aiozc.zeroconf, SERVICE_TYPE, handlers=[handler])


WIFI_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_SSID): str,
        probatio.Optional(CONF_PASSWORD, default=""): str,
    }
)
REPROVISION_SCHEMA = probatio.Schema({probatio.Optional("confirm", default=True): bool})


async def _find_device(
    hass: HomeAssistant, device_id: str, timeout: float
) -> tuple[str, int]:
    """Browse mDNS for the device_id and return its IPv4 endpoint."""
    aiozc = await async_get_async_instance(hass)
    found = asyncio.Event()
    names: list[str] = []

    @callback
    def _on_service(
        _zeroconf: Any,
        _service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change not in (ServiceStateChange.Added, ServiceStateChange.Updated):
            return
        if name not in names:
            names.append(name)
        found.set()

    browser = _service_browser(aiozc, _on_service)
    try:
        async with asyncio.timeout(timeout):
            while True:
                if not names:
                    found.clear()
                    await found.wait(1)
                    continue
                name = names.pop(0)
                info = await aiozc.async_get_service_info(SERVICE_TYPE, name)
                if info is None:
                    continue
                try:
                    discovery = parse_txt(info.decoded_properties)
                except ChiotProtocolError:
                    continue
                if discovery.device_id != device_id:
                    continue
                address = next(
                    (
                        str(ip)
                        for ip in info.ip_addresses_by_version(IPVersion.V4Only)
                        if not ip.is_link_local and not ip.is_unspecified
                    ),
                    None,
                )
                if address is None or info.port is None:
                    continue
                return address, info.port
    except TimeoutError as err:
        raise TimeoutError("Device did not announce itself") from err
    finally:
        await browser.async_cancel()


def _device_client(
    session: Any,
    host: str,
    port: int,
    device_id: str,
    token: str,
    anchors: Any,
) -> DeviceClient:
    """Return a DeviceClient; kept separate so tests can patch it."""
    return DeviceClient(session, host, port, device_id, token, anchors)


def _provisioning_client(
    ble_device: bluetooth.BLEDevice,
    anchors: Any,
    *,
    connect_timeout: float,
    response_timeout: float,
) -> ProvisioningClient:
    """Return a ProvisioningClient; kept separate so tests can patch it."""
    return ProvisioningClient(
        ble_device,
        anchors,
        connect_timeout=connect_timeout,
        response_timeout=response_timeout,
    )


class ChiotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CHIOT."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._address: str | None = None
        self._model_id: int | None = None
        self._discovered: dict[str, bluetooth.BluetoothServiceInfoBleak] = {}
        self._verified: VerifiedDevice | None = None
        self._existing_entry: ConfigEntry | None = None
        self._ssid: str | None = None
        self._password: str | None = None
        self._token: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._host: str | None = None
        self._port: int = WSS_PORT
        self._model: str | None = None
        self._error: str | None = None

    @override
    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle Bluetooth discovery of a device in provisioning mode."""
        if bluetooth.async_scanner_count(self.hass, connectable=True) == 0:
            return self.async_abort(reason="no_connectable_adapter")
        info = parse_advertisement_name(discovery_info.name)
        if info is None:
            return self.async_abort(reason="invalid_discovery_info")
        if not info.provisioning:
            return self.async_abort(reason="not_provisionable")
        self._address = discovery_info.address
        self._model_id = info.model_id
        await self.async_set_unique_id(
            f"ble_{discovery_info.address}", raise_on_progress=False
        )
        self.context["title_placeholders"] = {
            "name": discovery_info.name or discovery_info.address
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the discovered provisioning device."""
        if user_input is None:
            return self.async_show_form(step_id="bluetooth_confirm")
        return await self.async_step_verify()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a device that is advertising provisioning mode."""
        if bluetooth.async_scanner_count(self.hass, connectable=True) == 0:
            return self.async_abort(reason="no_connectable_adapter")
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            info = self._discovered.get(address)
            if info is None:
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._user_schema(),
                    errors={"base": "device_not_found"},
                )
            advertisement = parse_advertisement_name(info.name)
            assert advertisement is not None
            self._address = address
            self._model_id = advertisement.model_id
            await self.async_set_unique_id(f"ble_{address}", raise_on_progress=False)
            return await self.async_step_verify()

        in_progress = {
            progress["context"].get("unique_id")
            for progress in self._async_in_progress()
            if progress["flow_id"] != self.flow_id
        }
        for discovery in bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        ):
            if not is_provisioning_name(discovery.name):
                continue
            if f"ble_{discovery.address}" in in_progress:
                continue
            self._discovered[discovery.address] = discovery
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(step_id="user", data_schema=self._user_schema())

    def _user_schema(self) -> probatio.Schema:
        return probatio.Schema(
            {
                probatio.Required(CONF_ADDRESS): probatio.In(
                    {
                        address: f"{info.name} ({address})"
                        for address, info in self._discovered.items()
                    }
                )
            }
        )

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify the device identity over BLE without sending credentials."""
        if self._task is None:
            self._task = self.hass.async_create_task(
                self._async_verify(), eager_start=False
            )
        if not self._task.done():
            return self.async_show_progress(
                step_id="verify",
                progress_action="verify",
                progress_task=self._task,
            )
        self._task = None
        if self._error is not None:
            return self.async_abort(reason=self._error)
        assert self._verified is not None
        await self.async_set_unique_id(
            self._verified.device_id, raise_on_progress=False
        )
        self._existing_entry = (
            self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, self._verified.device_id
            )
        )
        if self._existing_entry is not None:
            return await self.async_step_reprovision_confirm()
        return await self.async_step_wifi()

    async def _async_verify(self) -> None:
        """Run the first BLE session: connect, handshake and disconnect."""
        assert self._address is not None
        try:
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self._address, connectable=True
            )
            if ble_device is None:
                self._error = "device_not_found"
                return
            anchors = await self.hass.async_add_executor_job(load_trust_anchors)
            client = _provisioning_client(
                ble_device,
                anchors,
                connect_timeout=BLE_VERIFY_TIMEOUT,
                response_timeout=BLE_VERIFY_TIMEOUT,
            )
            async with asyncio.timeout(BLE_VERIFY_TIMEOUT + 5):
                try:
                    self._verified = await client.connect_and_verify()
                finally:
                    await client.disconnect()
        except ChiotSecurityError:
            self._error = "untrusted_device"
        except ChiotUnsupportedVersion:
            self._error = "unsupported_version"
        except ChiotTimeoutError:
            self._error = "cannot_connect"
        except ChiotConnectionError as err:
            self._error = "mtu_too_small" if "MTU" in str(err) else "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error while verifying the device")
            self._error = "unknown"

    async def async_step_wifi(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Wi-Fi credentials after the identity is verified."""
        if user_input is not None:
            self._ssid = user_input[CONF_SSID]
            self._password = user_input[CONF_PASSWORD]
            return await self.async_step_provision()
        return self.async_show_form(step_id="wifi", data_schema=WIFI_SCHEMA)

    async def async_step_reprovision_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-provisioning an already configured device."""
        if user_input is None:
            return self.async_show_form(
                step_id="reprovision_confirm", data_schema=REPROVISION_SCHEMA
            )
        if not user_input.get("confirm", True):
            return self.async_abort(reason="already_configured")
        return await self.async_step_wifi()

    async def async_step_provision(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send the credentials over a fresh, re-verified BLE session."""
        if self._task is None:
            self._token = secrets.token_urlsafe(32)
            self._task = self.hass.async_create_task(
                self._async_provision(), eager_start=False
            )
        if not self._task.done():
            return self.async_show_progress(
                step_id="provision",
                progress_action="provision",
                progress_task=self._task,
            )
        self._task = None
        if self._error == "invalid_wifi":
            self._error = None
            return self.async_show_form(
                step_id="wifi", data_schema=WIFI_SCHEMA, errors={"base": "invalid_wifi"}
            )
        if self._error is not None:
            return self.async_abort(reason=self._error)
        return await self.async_step_wait_online()

    async def _async_provision(self) -> None:
        """Run the second BLE session and send the Wi-Fi configuration."""
        assert self._verified is not None
        assert self._address is not None
        assert self._ssid is not None
        assert self._token is not None
        try:
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self._address, connectable=True
            )
            if ble_device is None:
                self._error = "device_not_found"
                return
            anchors = await self.hass.async_add_executor_job(load_trust_anchors)
            client = _provisioning_client(
                ble_device,
                anchors,
                connect_timeout=BLE_VERIFY_TIMEOUT,
                response_timeout=BLE_PROVISION_TIMEOUT,
            )
            async with asyncio.timeout(BLE_PROVISION_TIMEOUT + 5):
                try:
                    verified = await client.connect_and_verify()
                    if verified.device_id != self._verified.device_id:
                        self._error = "device_changed"
                        return
                    try:
                        await client.provision(
                            self._ssid, self._password or "", self._token
                        )
                    except ChiotProvisionError as err:
                        if err.error_code == 1:
                            self._error = "invalid_wifi"
                        elif err.error_code is None or err.result_unknown:
                            # The device may still have joined; confirm below.
                            return
                        else:
                            self._error = "device_save_failed"
                finally:
                    await client.disconnect()
        except ChiotSecurityError:
            self._error = "untrusted_device"
        except ChiotTimeoutError:
            self._error = "cannot_connect"
        except ChiotConnectionError:
            self._error = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error while provisioning the device")
            self._error = "unknown"
        finally:
            self._password = None

    async def async_step_wait_online(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for the device to appear over mDNS and accept the token."""
        if self._task is None:
            self._task = self.hass.async_create_task(
                self._async_wait_online(), eager_start=False
            )
        if not self._task.done():
            return self.async_show_progress(
                step_id="wait_online",
                progress_action="wait_online",
                progress_task=self._task,
            )
        self._task = None
        if self._error is not None:
            return self.async_abort(reason=self._error)
        return self._async_finish()

    async def _async_wait_online(self) -> None:
        """Wait until the device is announced and accepts the new token."""
        assert self._verified is not None
        assert self._token is not None
        anchors = await self.hass.async_add_executor_job(load_trust_anchors)
        try:
            endpoint = await _find_device(
                self.hass, self._verified.device_id, WAIT_ONLINE_TIMEOUT
            )
        except TimeoutError:
            self._error = "not_online"
            return
        except ChiotProtocolError:
            _LOGGER.exception("Unexpected mDNS data while waiting for the device")
            self._error = "unknown"
            return
        host, port = endpoint
        try:
            model = await _probe_wss(
                self.hass,
                host,
                port,
                self._verified.device_id,
                self._token,
                anchors,
            )
        except ChiotAuthError:
            self._error = "auth_failed"
            return
        except ChiotConnectionError, ChiotTimeoutError:
            self._error = "not_online"
            return
        except ChiotSecurityError:
            self._error = "untrusted_device"
            return
        except Exception:
            _LOGGER.exception("Unexpected error while validating the WSS session")
            self._error = "unknown"
            return
        self._host = host
        self._port = port
        self._model = model

    def _async_finish(self) -> ConfigFlowResult:
        """Create or update the config entry."""
        assert self._verified is not None
        assert self._host is not None
        assert self._token is not None
        assert self._model is not None
        data = {
            CONF_DEVICE_ID: self._verified.device_id,
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_TOKEN: self._token,
            CONF_MODEL: self._model,
            CONF_MODEL_ID: self._model_id,
        }
        if self._existing_entry is not None:
            return self.async_update_reload_and_abort(
                self._existing_entry,
                data_updates=data,
                reason="reprovision_successful",
            )
        for progress in self._async_in_progress(include_uninitialized=True):
            if (
                progress["flow_id"] != self.flow_id
                and progress["context"].get("unique_id") == self._verified.device_id
            ):
                self.hass.config_entries.flow.async_abort(progress["flow_id"])
        return self.async_create_entry(title=self._model, data=data)

    @override
    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle mDNS discovery of a provisioned device."""
        try:
            discovery = parse_txt(discovery_info.properties)
        except ChiotProtocolError:
            return self.async_abort(reason="invalid_discovery_info")
        address = next(
            (
                str(ip)
                for ip in discovery_info.ip_addresses
                if isinstance(ip, IPv4Address)
                and not ip.is_link_local
                and not ip.is_unspecified
            ),
            None,
        )
        if address is None or discovery_info.port is None:
            return self.async_abort(reason="invalid_discovery_info")
        await self.async_set_unique_id(discovery.device_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: address, CONF_PORT: discovery_info.port}
        )
        return self.async_abort(reason="provision_required")

    @callback
    @override
    def async_remove(self) -> None:
        """Cancel a running background task when the flow is removed."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self._password = None
