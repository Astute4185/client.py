from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from deebot_client.commands.json import COMMANDS
from deebot_client.commands.json.play_sound import NgiotPlaySound, PlaySound

from . import assert_execute_command

if TYPE_CHECKING:
    from unittest.mock import Mock

    from deebot_client.models import ApiDeviceInfo


async def test_PlaySound() -> None:
    await assert_execute_command(PlaySound(), {"count": 1, "sid": 30})


@pytest.mark.parametrize("code", [0, "0", 1])
async def test_ngiot_play_sound(
    authenticator: Mock,
    api_device_info: ApiDeviceInfo,
    event_bus_mock: Mock,
    code: int | str,
) -> None:
    authenticator.post_ngiot.return_value = {"body": {"code": code, "msg": "ok"}}
    result = await NgiotPlaySound().execute(
        authenticator, api_device_info, event_bus_mock
    )
    assert result.device_reached == (code in (0, "0"))
    authenticator.post_ngiot.assert_awaited_once_with(
        api_device_info, "40019", {"seek": True}
    )
    authenticator.post_authenticated.assert_not_called()
    event_bus_mock.notify.assert_not_called()


def test_ngiot_registry_unchanged() -> None:
    assert COMMANDS[PlaySound.NAME] is PlaySound
    assert NgiotPlaySound.NAME not in COMMANDS
