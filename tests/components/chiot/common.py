"""Shared constants for the CHIOT tests."""

from chiot_homeassistant_client import DeviceInfo

from tests.common import MockConfigEntry

DOMAIN = "chiot"
CONF_DEVICE_ID = "device_id"
CONF_MODEL = "model"
CONF_MODEL_ID = "model_id"
CONF_TOKEN = "token"
CONF_SSID = "ssid"
CONF_PASSWORD = "password"

DEVICE_ID = "euplug-0123456789abcdef"
ADDRESS = "AA:BB:CC:DD:EE:FF"
WSS_PORT = 8443
TOKEN = "local-token-0123456789abcdef"

DEVICE_INFO = DeviceInfo(
    device_id=DEVICE_ID,
    manufacturer="CHIOT",
    model="HPL-JA21",
    hardware_version="1.0",
    software_version="2.0.1",
    mac="AA:BB:CC:DD:EE:FF",
)

SERVICES_ON_DEVICE = [
    "DeviceInfo",
    "SmartSocketCommon",
    "ElectricityCommon",
    "DoNotDisturbCommon",
    "PoweroffMemoryCommon",
    "IdentifyCommon",
    "WifiCommon",
]

INITIAL_STATE = {
    "SmartSocketCommon": {"power-switch_1": 0},
    "ElectricityCommon": {
        "power": 0,
        "voltage": 230000,
        "current": 0,
        "power-consumption": 0,
        "rated-power": 2500,
        "power-alarm-enable": 1,
        "power-consumption-alarm-enable": 0,
    },
    "DoNotDisturbCommon": {"doNotDisturb": 0},
    "PoweroffMemoryCommon": {"poweroff_memory": 1},
    "WifiCommon": {"rssi": -55},
}


def config_entry() -> MockConfigEntry:
    """Return a configured entry for the test device."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=DEVICE_INFO.model,
        unique_id=DEVICE_ID,
        data={
            CONF_DEVICE_ID: DEVICE_ID,
            "host": "192.168.1.50",
            "port": WSS_PORT,
            CONF_TOKEN: TOKEN,
            CONF_MODEL: DEVICE_INFO.model,
            CONF_MODEL_ID: 1001,
        },
    )
