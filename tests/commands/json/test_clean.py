from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

from deebot_client.commands.json import COMMANDS, GetCleanInfo
from deebot_client.commands.json.clean import (
    Clean,
    CleanArea,
    CleanAreaV2,
    CleanV2,
    GetCleanInfoV2,
    NgiotClean,
    NgiotCleanArea,
    NgiotGetCleanInfo,
)
from deebot_client.event_bus import EventBus
from deebot_client.events import FirmwareEvent, StateEvent
from deebot_client.models import ApiDeviceInfo, CleanAction, CleanMode, State
from tests.helpers import get_request_json, get_success_body

from . import assert_command, assert_execute_command

if TYPE_CHECKING:
    from deebot_client.authentication import Authenticator


@pytest.mark.parametrize(
    ("command", "data", "expected"),
    [
        (
            GetCleanInfo,
            get_request_json(get_success_body({"trigger": "none", "state": "idle"})),
            StateEvent(State.IDLE),
        ),
        (
            GetCleanInfoV2,
            get_request_json(get_success_body({"trigger": "none", "state": "idle"})),
            StateEvent(State.IDLE),
        ),
        (
            GetCleanInfoV2,
            get_request_json(
                get_success_body(
                    {
                        "trigger": "none",
                        "state": "washing",
                        "cleanState": {
                            "cid": "122",
                            "router": "plan",
                            "motionState": "pause",
                            "content": {"subContent": {"type": "auto"}},
                            "cmode": 2,
                        },
                    }
                )
            ),
            StateEvent(State.PAUSED),
        ),
    ],
)
async def test_GetCleanInfo(
    command: type[GetCleanInfo],
    data: tuple[dict[str, Any], FirmwareEvent],
    expected: StateEvent,
) -> None:
    json, firmware_event = data
    await assert_command(command(), json, (firmware_event, expected))


@pytest.mark.parametrize("command_type", [Clean, CleanV2])
@pytest.mark.parametrize(
    ("action", "state", "expected"),
    [
        (CleanAction.START, None, CleanAction.START),
        (CleanAction.START, State.PAUSED, CleanAction.RESUME),
        (CleanAction.START, State.DOCKED, CleanAction.START),
        (CleanAction.RESUME, None, CleanAction.RESUME),
        (CleanAction.RESUME, State.PAUSED, CleanAction.RESUME),
        (CleanAction.RESUME, State.DOCKED, CleanAction.START),
        (CleanAction.STOP, None, CleanAction.STOP),
        (CleanAction.STOP, State.CLEANING, CleanAction.STOP),
        (CleanAction.STOP, State.PAUSED, CleanAction.STOP),
        (CleanAction.STOP, State.DOCKED, CleanAction.STOP),
    ],
)
async def test_Clean_act(
    authenticator: Authenticator,
    api_device_info: ApiDeviceInfo,
    command_type: type[Clean],
    action: CleanAction,
    state: State | None,
    expected: CleanAction,
) -> None:
    event_bus = Mock(spec_set=EventBus)
    event_bus.get_last_event.return_value = (
        StateEvent(state) if state is not None else None
    )
    command = command_type(action)

    await command.execute(authenticator, api_device_info, event_bus)

    assert isinstance(command._args, dict)
    assert command._args["act"] == expected.value

    if command_type is CleanV2:
        assert isinstance(command._args["content"], dict)


@pytest.mark.parametrize(
    ("command", "args"),
    [
        (
            CleanArea(CleanMode.SPOT_AREA, [5, 8]),
            {"act": "start", "type": "spotArea", "content": "5,8", "count": 1},
        ),
        (
            CleanAreaV2(CleanMode.SPOT_AREA, [5, 8]),
            {"act": "start", "content": {"type": "spotArea", "value": "5,8"}},
        ),
        (
            CleanArea(CleanMode.CUSTOM_AREA, [1580.0, -4087.0, 3833.0, -7525.0]),
            {
                "act": "start",
                "type": "customArea",
                "content": "1580.0,-4087.0,3833.0,-7525.0",
                "count": 1,
            },
        ),
        (
            CleanAreaV2(CleanMode.CUSTOM_AREA, [1580.0, -4087.0, 3833.0, -7525.0]),
            {
                "act": "start",
                "content": {
                    "type": "customArea",
                    "value": "1580.0,-4087.0,3833.0,-7525.0",
                },
            },
        ),
        (
            CleanAreaV2(CleanMode.FREE_CLEAN, [5, 8]),
            {
                "act": "start",
                "content": {"type": "freeClean", "value": "1,5,8"},
            },
        ),
        (
            CleanAreaV2(CleanMode.FREE_CLEAN, [0], cleanings=2),
            {
                "act": "start",
                "content": {"type": "freeClean", "value": "2,0"},
            },
        ),
    ],
    ids=[
        "Rooms",
        "Rooms V2",
        "Coordinates",
        "Coordinates V2",
        "FreeClean",
        "FreeClean single room 2x",
    ],
)
async def test_CleanArea(
    command: CleanArea | CleanAreaV2, args: dict[str, str]
) -> None:
    await assert_execute_command(command, args)


@pytest.mark.parametrize(
    ("action", "state", "apn", "payload"),
    [
        (CleanAction.START, None, "40001", {"cleanSwitch": True, "cleanMode": "smart"}),
        (CleanAction.START, State.PAUSED, "40011", {"pauseSwitch": False}),
        (CleanAction.RESUME, None, "40011", {"pauseSwitch": False}),
        (CleanAction.RESUME, State.PAUSED, "40011", {"pauseSwitch": False}),
        (
            CleanAction.RESUME,
            State.DOCKED,
            "40001",
            {"cleanSwitch": True, "cleanMode": "smart"},
        ),
        (CleanAction.PAUSE, State.CLEANING, "40009", {"pauseSwitch": True}),
        (CleanAction.STOP, State.CLEANING, "40013", {"chargeSwitch": True}),
    ],
)
@pytest.mark.parametrize("code", [0, "0"])
async def test_ngiot_clean_action(
    authenticator: Mock,
    api_device_info: ApiDeviceInfo,
    event_bus_mock: Mock,
    action: CleanAction,
    state: State | None,
    apn: str,
    payload: dict[str, Any],
    code: int | str,
) -> None:
    event_bus_mock.get_last_event.return_value = StateEvent(state) if state else None
    response = {"body": {"code": code, "msg": "ok"}}
    authenticator.post_ngiot.return_value = response

    result = await NgiotClean(action).execute(
        authenticator, api_device_info, event_bus_mock
    )

    assert result.device_reached
    assert result.raw_response == response
    authenticator.post_ngiot.assert_awaited_once_with(api_device_info, apn, payload)
    authenticator.post_authenticated.assert_not_called()
    event_bus_mock.notify.assert_not_called()


async def test_ngiot_clean_reused_command(
    authenticator: Mock, api_device_info: ApiDeviceInfo, event_bus_mock: Mock
) -> None:
    command = NgiotClean(CleanAction.START)
    authenticator.post_ngiot.return_value = {"body": {"code": 0, "msg": "ok"}}
    event_bus_mock.get_last_event.return_value = StateEvent(State.PAUSED)
    result = await command.execute(authenticator, api_device_info, event_bus_mock)
    assert result.device_reached
    authenticator.post_ngiot.assert_awaited_once_with(
        api_device_info, "40011", {"pauseSwitch": False}
    )
    authenticator.post_ngiot.reset_mock()
    event_bus_mock.get_last_event.return_value = StateEvent(State.DOCKED)
    result = await command.execute(authenticator, api_device_info, event_bus_mock)
    assert result.device_reached
    authenticator.post_ngiot.assert_awaited_once_with(
        api_device_info, "40001", {"cleanSwitch": True, "cleanMode": "smart"}
    )


@pytest.mark.parametrize("state", [State.PAUSED, State.DOCKED])
async def test_ngiot_clean_area(
    authenticator: Mock,
    api_device_info: ApiDeviceInfo,
    event_bus_mock: Mock,
    state: State,
) -> None:
    event_bus_mock.get_last_event.return_value = StateEvent(state)
    authenticator.post_ngiot.return_value = {"body": {"code": 0, "msg": "ok"}}
    result = await NgiotCleanArea(CleanMode.SPOT_AREA, [1, 2]).execute(
        authenticator, api_device_info, event_bus_mock
    )
    assert result.device_reached
    authenticator.post_ngiot.assert_awaited_once_with(
        api_device_info,
        "40007",
        {"cleanSwitch": True, "cleanMode": "area", "cleanValues": [1, 2]},
    )
    event_bus_mock.notify.assert_not_called()
    authenticator.post_authenticated.assert_not_called()


@pytest.mark.parametrize(
    "mode", [CleanMode.AUTO, CleanMode.CUSTOM_AREA, CleanMode.FREE_CLEAN]
)
def test_ngiot_unsupported_area_mode(mode: CleanMode) -> None:
    with pytest.raises(ValueError, match="room-ID cleaning only"):
        NgiotCleanArea(mode, [1])


@pytest.mark.parametrize("cleanings", [0, 2])
def test_ngiot_unsupported_repeat_count(cleanings: int) -> None:
    with pytest.raises(ValueError, match="single pass only"):
        NgiotCleanArea(CleanMode.SPOT_AREA, [1], cleanings)


@pytest.mark.parametrize("area", [[], [-1]])
def test_ngiot_invalid_area_values(area: list[int | float]) -> None:
    with pytest.raises(ValueError, match="non-empty, non-negative"):
        NgiotCleanArea(CleanMode.SPOT_AREA, area)


@pytest.mark.parametrize("area", [[True], [1.5]])
def test_ngiot_invalid_area_type(area: list[int | float]) -> None:
    with pytest.raises(TypeError, match="room IDs must be integers"):
        NgiotCleanArea(CleanMode.SPOT_AREA, area)


@pytest.mark.parametrize(
    ("work_mode", "pause", "charge", "expected"),
    [
        ("stop", False, True, State.DOCKED),
        ("stop", False, False, State.IDLE),
        ("auto_pause", True, False, State.PAUSED),
        ("auto_pause", False, False, State.PAUSED),
        ("smart", False, False, State.CLEANING),
        ("smart", True, False, State.PAUSED),
        ("area", False, False, State.CLEANING),
        ("goCharge", False, False, State.RETURNING),
        ("idle", False, True, State.DOCKED),
    ],
)
async def test_ngiot_clean_info(
    authenticator: Mock,
    api_device_info: ApiDeviceInfo,
    event_bus_mock: Mock,
    work_mode: str,
    pause: bool,
    charge: bool,
    expected: State,
) -> None:
    authenticator.post_ngiot.return_value = {
        "body": {
            "code": 0,
            "data": {
                "workMode": work_mode,
                "pauseSwitch": pause,
                "chargeStatus": charge,
            },
        }
    }
    result = await NgiotGetCleanInfo().execute(
        authenticator, api_device_info, event_bus_mock
    )
    assert result.device_reached
    authenticator.post_ngiot.assert_awaited_once_with(
        api_device_info,
        "10001",
        {"fields": ["workMode", "pauseSwitch", "chargeStatus"]},
    )
    event_bus_mock.notify.assert_called_once_with(StateEvent(expected))
    authenticator.post_authenticated.assert_not_called()


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"workMode": "stop", "pauseSwitch": False},
        {"workMode": "stop", "chargeStatus": True},
        {"workMode": "stop", "pauseSwitch": "false", "chargeStatus": True},
        {"workMode": "unknown", "pauseSwitch": False, "chargeStatus": False},
        {"workMode": None, "pauseSwitch": False, "chargeStatus": False},
    ],
)
async def test_ngiot_invalid_clean_info(
    authenticator: Mock,
    api_device_info: ApiDeviceInfo,
    event_bus_mock: Mock,
    data: dict[str, Any],
) -> None:
    authenticator.post_ngiot.return_value = {"body": {"code": 0, "data": data}}
    result = await NgiotGetCleanInfo().execute(
        authenticator, api_device_info, event_bus_mock
    )
    assert not result.device_reached
    event_bus_mock.notify.assert_not_called()


async def test_ngiot_clean_rejected(
    authenticator: Mock, api_device_info: ApiDeviceInfo, event_bus_mock: Mock
) -> None:
    event_bus_mock.get_last_event.return_value = None
    authenticator.post_ngiot.return_value = {"body": {"code": 1, "msg": "fail"}}
    result = await NgiotClean(CleanAction.START).execute(
        authenticator, api_device_info, event_bus_mock
    )
    assert not result.device_reached
    event_bus_mock.notify.assert_not_called()


def test_ngiot_clean_registry_unchanged() -> None:
    assert COMMANDS[Clean.NAME] is CleanArea
    assert COMMANDS[GetCleanInfo.NAME] is GetCleanInfo
    assert NgiotClean.NAME not in COMMANDS
    assert NgiotCleanArea.NAME not in COMMANDS
    assert NgiotGetCleanInfo.NAME not in COMMANDS
