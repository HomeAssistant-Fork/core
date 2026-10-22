"""Coordinator for a single CHIOT device connection."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from chiot_homeassistant_client import (
    ChiotAuthError,
    ChiotConnectionError,
    ChiotDeviceError,
    ChiotError,
    ChiotNotSupportedError,
    ChiotProtocolError,
    ChiotSecurityError,
    ChiotTimeoutError,
    DeviceClient,
    DeviceInfo,
    Notification,
)
from chiot_homeassistant_client.services import SERVICES

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_DEVICE_ID, CONF_TOKEN, DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAYS = (2, 5, 10, 30, 60)

type ChiotConfigEntry = ConfigEntry[ChiotCoordinator]


@dataclass
class ChiotState:
    """Cached device state shared by every entity."""

    info: DeviceInfo
    services: frozenset[str]
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    unavailable: set[str] = field(default_factory=set)


class ChiotCoordinator(DataUpdateCoordinator[ChiotState]):
    """Keep one WSS connection and one state cache per config entry."""

    config_entry: ChiotConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ChiotConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_DEVICE_ID]}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self._client: DeviceClient | None = None
        self._anchors: Any = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._shutdown = False
        self._reconnect_count = 0

    @property
    def device_id(self) -> str:
        """Return the stable device identity."""
        return self.config_entry.data[CONF_DEVICE_ID]

    @property
    def client(self) -> DeviceClient | None:
        """Return the current client, if any."""
        return self._client

    @property
    def reconnect_count(self) -> int:
        """Return how many times the connection was re-established."""
        return self._reconnect_count

    def _new_client(self) -> DeviceClient:
        client = DeviceClient(
            async_get_clientsession(self.hass),
            self.config_entry.data[CONF_HOST],
            self.config_entry.data[CONF_PORT],
            self.device_id,
            self.config_entry.data[CONF_TOKEN],
            self._anchors,
        )
        client.set_notification_callback(self._handle_notification)
        client.set_disconnect_callback(self._handle_disconnect)
        return client

    async def async_start(self, anchors: Any) -> None:
        """Store the trust anchors and create the client."""
        self._anchors = anchors
        self._client = self._new_client()

    async def _async_update_data(self) -> ChiotState:
        """Connect if needed and refresh every supported service."""
        client = self._client
        if client is None:
            raise UpdateFailed("Client is not initialized")
        try:
            if client.closed:
                await self._async_connect()
            info = await client.get_device_info()
            _verify_device_id(info, self.device_id)
            services = await client.get_services()
            states: dict[str, dict[str, Any]] = {}
            unavailable: set[str] = set()
            for service_name in services:
                if not SERVICES[service_name].property_names:
                    continue
                try:
                    states[service_name] = await client.get_properties(service_name)
                except ChiotNotSupportedError:
                    unavailable.add(service_name)
                    _LOGGER.debug("Device reports %s as not supported", service_name)
                except ChiotDeviceError:
                    _LOGGER.warning("Reading %s failed", service_name, exc_info=True)
                    unavailable.add(service_name)
            previous = self.data.services if self.data else frozenset()
            if previous and frozenset(services) != previous:
                _LOGGER.debug("Service set changed, reloading the config entry")
                self.hass.config_entries.async_schedule_reload(
                    self.config_entry.entry_id
                )
            return ChiotState(
                info=info,
                services=frozenset(services),
                states=states,
                unavailable=unavailable,
            )
        except ChiotAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ChiotSecurityError as err:
            raise UpdateFailed(f"Device identity verification failed: {err}") from err
        except (ChiotConnectionError, ChiotTimeoutError) as err:
            raise UpdateFailed(f"Unable to communicate with the device: {err}") from err

    async def _async_connect(self) -> None:
        """Create a fresh client when the previous one is closed."""
        client = self._client
        if client is None or client.closed:
            client = self._new_client()
            self._client = client
        await client.connect()

    def _handle_notification(self, notification: Notification) -> None:
        """Merge property notifications into the cached state."""
        if self.data is None:
            return
        states = dict(self.data.states)
        updated = False
        for service in notification.services:
            if service.get("type") != "propertyNotify":
                continue
            name = service.get("serviceName")
            params = service.get("params")
            if not isinstance(name, str) or name not in self.data.services:
                continue
            if not isinstance(params, dict):
                continue
            try:
                validated = _parse_notification(name, params)
            except ChiotProtocolError:
                _LOGGER.warning("Discarding an invalid %s notification", name)
                continue
            states[name] = {**states.get(name, {}), **validated}
            updated = True
        if updated:
            self.async_set_updated_data(
                ChiotState(
                    info=self.data.info,
                    services=self.data.services,
                    states=states,
                    unavailable=self.data.unavailable,
                )
            )

    def _handle_disconnect(self, reason: str) -> None:
        """Mark the device unavailable and schedule a reconnect."""
        if self._shutdown:
            return
        _LOGGER.info("CHIOT connection lost: %s", reason)
        self.last_update_success = False
        self.async_update_listeners()
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.hass.async_create_task(
                self._async_reconnect(), eager_start=False
            )

    async def _async_reconnect(self) -> None:
        """Reconnect with backoff until success or a terminal error."""
        attempt = 0
        while not self._shutdown:
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            attempt += 1
            await asyncio.sleep(delay)
            try:
                await self._async_connect()
            except ChiotAuthError:
                _LOGGER.warning("Device rejected the stored token")
                self.config_entry.async_start_reauth(self.hass)
                return
            except ChiotSecurityError:
                _LOGGER.error("Device identity verification failed, giving up")
                return
            except (ChiotConnectionError, ChiotTimeoutError) as err:
                _LOGGER.debug("Reconnect attempt failed: %s", err)
                continue
            except ChiotError as err:
                _LOGGER.debug("Reconnect attempt failed: %s", err)
                continue
            self._reconnect_count += 1
            _LOGGER.info("CHIOT connection restored")
            await self.async_request_refresh()
            return

    async def async_run(
        self,
        command: Callable[[DeviceClient], Awaitable[Any]],
        service_name: str,
    ) -> None:
        """Run a command, then re-read only the affected service."""
        client = self._client
        if client is None or client.closed:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_unavailable"
            )
        try:
            await command(client)
        except ChiotNotSupportedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_supported"
            ) from err
        except ChiotDeviceError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_rejected",
                translation_placeholders={"code": str(err.code)},
            ) from err
        except (ChiotConnectionError, ChiotTimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_unavailable"
            ) from err
        except ChiotError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_error",
                translation_placeholders={"message": str(err)},
            ) from err
        await self._async_refresh_service(service_name)

    async def _async_refresh_service(self, service_name: str) -> None:
        """Re-read one service and publish the merged state."""
        client = self._client
        if client is None or self.data is None or service_name not in client.services:
            return
        if not SERVICES[service_name].property_names:
            return
        try:
            values = await client.get_properties(service_name)
        except ChiotError as err:
            _LOGGER.debug("Refreshing %s after a command failed: %s", service_name, err)
            return
        states = dict(self.data.states)
        states[service_name] = values
        self.async_set_updated_data(
            ChiotState(
                info=self.data.info,
                services=self.data.services,
                states=states,
                unavailable=self.data.unavailable - {service_name},
            )
        )

    async def async_shutdown(self) -> None:
        """Stop reconnecting and close the connection."""
        self._shutdown = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._client is not None:
            await self._client.close()
            self._client = None


def _parse_notification(service_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Validate a notification against the library service contract."""
    return SERVICES[service_name].parse_properties(params)


def _verify_device_id(info: DeviceInfo, device_id: str) -> None:
    """Raise when the device reports a different identity."""
    if info.device_id != device_id:
        raise ChiotSecurityError("DeviceInfo deviceId does not match the entry")
