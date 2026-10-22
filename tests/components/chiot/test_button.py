"""Button entity tests for the CHIOT integration."""

from unittest.mock import AsyncMock, patch

from homeassistant.components.button import BUTTONS, ChiotButton


def test_button_descriptions() -> None:
    """Identify and reboot map to the documented device methods."""
    by_key = {description.key: description for description in BUTTONS}
    assert by_key["identify"].service == "IdentifyCommon"
    assert by_key["identify"].subtype == "identify"
    assert by_key["reboot"].service == "DeviceInfo"
    assert by_key["reboot"].subtype == "reboot"


async def test_button_press(coordinator) -> None:
    """Pressing a button delegates to the coordinator."""
    button = ChiotButton(coordinator, BUTTONS[0])
    assert button.unique_id == f"{coordinator.device_id}_IdentifyCommon_identify"
    with patch.object(coordinator, "async_run", new=AsyncMock()) as run:
        await button.async_press()
    run.assert_awaited_once()
