"""The CHIOT integration."""

from chiot_homeassistant_client import (
    ChiotAuthError,
    ChiotConnectionError,
    ChiotSecurityError,
    ChiotTimeoutError,
    TrustAnchors,
    load_trust_anchors,
)

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)

from .const import DOMAIN, PLATFORMS
from .coordinator import ChiotConfigEntry, ChiotCoordinator


async def async_get_trust_anchors(hass: HomeAssistant) -> TrustAnchors:
    """Return the packaged trust anchors, loading them once per instance."""
    anchors: TrustAnchors | None = hass.data.get(DOMAIN)
    if anchors is None:
        anchors = await hass.async_add_executor_job(load_trust_anchors)
        hass.data[DOMAIN] = anchors
    return anchors


async def async_setup_entry(hass: HomeAssistant, entry: ChiotConfigEntry) -> bool:
    """Set up CHIOT from a config entry."""
    try:
        anchors = await async_get_trust_anchors(hass)
    except ChiotSecurityError as err:
        raise ConfigEntryError(f"CHIOT trust anchors are unavailable: {err}") from err
    coordinator = ChiotCoordinator(hass, entry)
    await coordinator.async_start(anchors)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ChiotAuthError as err:
        await coordinator.async_shutdown()
        raise ConfigEntryAuthFailed from err
    except (ChiotConnectionError, ChiotTimeoutError) as err:
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady from err
    except ChiotSecurityError as err:
        await coordinator.async_shutdown()
        raise ConfigEntryError(f"Device identity verification failed: {err}") from err
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.async_shutdown)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ChiotConfigEntry) -> bool:
    """Unload a CHIOT config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown()
    return unload_ok
