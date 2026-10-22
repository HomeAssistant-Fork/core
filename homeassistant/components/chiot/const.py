"""Constants for the CHIOT integration."""

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "chiot"

CONF_DEVICE_ID: Final = "device_id"
CONF_MODEL_ID: Final = "model_id"
CONF_TOKEN: Final = "token"
CONF_SSID: Final = "ssid"
CONF_PASSWORD: Final = "password"

BLE_VERIFY_TIMEOUT: Final = 30
BLE_PROVISION_TIMEOUT: Final = 45
WAIT_ONLINE_TIMEOUT: Final = 120
WAIT_ONLINE_POLL_INTERVAL: Final = 2.0
WSS_PORT: Final = 8443

UPDATE_INTERVAL: Final = 300

PLATFORMS: Final[tuple[Platform, ...]] = (
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BUTTON,
)
