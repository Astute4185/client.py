from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from deebot_client.commands.json import COMMANDS, GetChargeState
from deebot_client.commands.json.charge_state import NgiotGetChargeState
from deebot_client.events import StateEvent
from deebot_client.models import State
from tests.helpers import get_request_json, get_success_body

from . import assert_command

if TYPE_CHECKING:
    from unittest.mock import Mock

    from deebot_client.events import FirmwareEvent
    from deebot_client.events.base import Event
    from deebot_client.models import ApiDeviceInfo


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (get_request_json(get_success_body({"isCharging": 0, "mode": "slot"})), None),
    ],
)
async def test_GetChargeState(
    data: tuple[dict[str, Any], FirmwareEvent], expected: StateEvent | None
) -> None:
    json, firmware_event = data
    events: list[Event] = [firmware_event]
    if expected:
        events.append(expected)
    await assert_command(GetChargeState(), json, events)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("30007", StateEvent(State.DOCKED)),
        ("3", StateEvent(State.ERROR)),
        ("5", StateEvent(State.ERROR)),
    ],
)
async def test_GetChargeState_fail(code: str, expected: StateEvent) -> None:
    json, firmware_event = get_request_json({"code": code, "msg": "fail"})
    await assert_command(GetChargeState(), json, (firmware_event, expected))


@pytest.mark.parametrize("charge_status", [True, False])
@pytest.mark.parametrize("code", [0, "0"])
async def test_ngiot_charge_state(
    authenticator: Mock,
    api_device_info: ApiDeviceInfo,
    event_bus_mock: Mock,
    charge_status: bool,
    code: int | str,
) -> None:
    response = {"body": {"code": code, "data": {"chargeStatus": charge_status}}}
    authenticator.post_ngiot.return_value = response
    result = await NgiotGetChargeState().execute(
        authenticator, api_device_info, event_bus_mock
    )
    assert result.device_reached
    assert result.raw_response == response
    authenticator.post_ngiot.assert_awaited_once_with(
        api_device_info, "10001", {"fields": ["chargeStatus"]}
    )
    if charge_status:
        event_bus_mock.notify.assert_called_once_with(StateEvent(State.DOCKED))
    else:
        event_bus_mock.notify.assert_not_called()


@pytest.mark.parametrize(
    "data", [{}, {"chargeStatus": None}, {"chargeStatus": "true"}, {"chargeStatus": 1}]
)
async def test_ngiot_invalid_charge_state(
    authenticator: Mock,
    api_device_info: ApiDeviceInfo,
    event_bus_mock: Mock,
    data: dict[str, Any],
) -> None:
    authenticator.post_ngiot.return_value = {"body": {"code": 0, "data": data}}
    result = await NgiotGetChargeState().execute(
        authenticator, api_device_info, event_bus_mock
    )
    assert not result.device_reached
    event_bus_mock.notify.assert_not_called()


def test_ngiot_registry_unchanged() -> None:
    assert COMMANDS[GetChargeState.NAME] is GetChargeState
    assert NgiotGetChargeState.NAME not in COMMANDS
