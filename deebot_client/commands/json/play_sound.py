"""Play sound commands."""

from __future__ import annotations

from .common import ExecuteCommand, NgiotJsonCommand


class PlaySound(ExecuteCommand):
    """Play sound command."""

    NAME = "playSound"

    def __init__(self) -> None:
        super().__init__({"count": 1, "sid": 30})


class NgiotPlaySound(NgiotJsonCommand, ExecuteCommand):
    """Trigger the robot locator sound through NGIOT."""

    NAME = "ngiotPlaySound"
    APN = "40019"

    def __init__(self) -> None:
        super().__init__({"seek": True})
