"""Switch entity tests for the CHIOT integration."""

from unittest.mock import AsyncMock, patch

from chiot_homeassistant_client import ChiotConnectionError

from homeassistant.components.chiot.coordinator import ChiotState
from homeassistant.components.chiot.switch import SWITCHES, ChiotSwitch
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.exceptions import HomeAssistantError


async def test_outlet_switch(coordinator, mock_chiot_client: AsyncMock) -> None:
    """The main outlet switch uses the socket property and method."""
    switch = ChiotSwitch(coordinator, SWITCHES[0])
    assert (
        switch.unique_id == f"{coordinator.device_id}_SmartSocketCommon_power-switch_1"
    )
    assert switch.device_class is SwitchDeviceClass.OUTLET
    assert switch.name is None
    assert switch.is_on is False
    with patch.object(coordinator, "async_run", new=AsyncMock()) as run:
        await switch.async_turn_on()
    run.assert_awaited_once()


async def test_config_switches(coordinator) -> None:
    """Do-not-disturb, memory and alarm switches write their own property."""
    for index in (1, 2, 3, 4):
        switch = ChiotSwitch(coordinator, SWITCHES[index])
        assert switch.entity_description.translation_key
        assert switch.unique_id.startswith(coordinator.device_id)
        assert switch.is_on is (
            switch.service_state[switch.entity_description.key] == 1
        )


async def test_switch_not_created_without_service(coordinator) -> None:
    """A service missing from Portfolio makes the entity unavailable."""
    coordinator.data = ChiotState(
        info=coordinator.data.info,
        services=frozenset({"DeviceInfo"}),
        states=coordinator.data.states,
    )
    switch = ChiotSwitch(coordinator, SWITCHES[0])
    assert switch.available is False


async def test_command_error_surfaces(
    coordinator, mock_chiot_client: AsyncMock
) -> None:
    """Device errors from commands propagate as HomeAssistantError."""

    coordinator._client = mock_chiot_client
    mock_chiot_client.set_properties.side_effect = ChiotConnectionError("gone")
    switch = ChiotSwitch(coordinator, SWITCHES[0])
    try:
        await switch.async_turn_off()
    except HomeAssistantError:
        pass
    else:
        raise AssertionError("expected HomeAssistantError")
