"""Clean commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deebot_client.events import StateEvent
from deebot_client.logging_filter import get_logger
from deebot_client.message import HandlingResult, MessageBodyDataDict
from deebot_client.models import ApiDeviceInfo, CleanAction, CleanMode, State

from .common import (
    ExecuteCommand,
    JsonCommandWithMessageHandling,
    JsonGetCommand,
    NgiotJsonCommand,
)

if TYPE_CHECKING:
    from deebot_client.authentication import Authenticator
    from deebot_client.event_bus import EventBus

_LOGGER = get_logger(__name__)


class Clean(ExecuteCommand):
    """Clean command."""

    NAME = "clean"

    def __init__(self, action: CleanAction) -> None:
        super().__init__(self._get_args(action))

    async def _execute(
        self,
        authenticator: Authenticator,
        device_info: ApiDeviceInfo,
        event_bus: EventBus,
    ) -> tuple[HandlingResult, dict[str, Any]]:
        """Execute command."""
        state = event_bus.get_last_event(StateEvent)
        if state and isinstance(self._args, dict):
            if (
                self._args["act"] == CleanAction.RESUME.value
                and state.state != State.PAUSED
            ):
                self._args = self._get_args(CleanAction.START)
            elif (
                self._args["act"] == CleanAction.START.value
                and state.state == State.PAUSED
            ):
                self._args = self._get_args(CleanAction.RESUME)

        return await super()._execute(authenticator, device_info, event_bus)

    def _get_args(self, action: CleanAction) -> dict[str, Any]:
        args = {"act": action.value}
        if action == CleanAction.START:
            args["type"] = CleanMode.AUTO.value
        return args


class CleanArea(Clean):
    """Clean area command."""

    def __init__(
        self, mode: CleanMode, area: list[int | float], cleanings: int = 1
    ) -> None:
        self._additional_args = {
            "type": mode.value,
            "content": ",".join(str(i) for i in area),
            "count": cleanings,
        }
        super().__init__(CleanAction.START)

    def _get_args(self, action: CleanAction) -> dict[str, Any]:
        args = super()._get_args(action)
        if action == CleanAction.START:
            args.update(self._additional_args)
        return args


class CleanV2(Clean):
    """Clean V2 command."""

    NAME = "clean_V2"

    def _get_args(self, action: CleanAction) -> dict[str, Any]:
        content: dict[str, str] = {}
        args = {"act": action.value, "content": content}
        match action:
            case CleanAction.START:
                content["type"] = CleanMode.AUTO.value
            case CleanAction.STOP | CleanAction.PAUSE:
                content["type"] = ""
        return args


class CleanAreaV2(CleanV2):
    """Clean area command."""

    def __init__(
        self, mode: CleanMode, area: list[int | float], cleanings: int = 1
    ) -> None:
        value = ",".join(str(i) for i in area)
        if mode == CleanMode.FREE_CLEAN:
            value = f"{cleanings},{value}"
        self._additional_content = {
            "type": mode.value,
            "value": value,
        }
        super().__init__(CleanAction.START)

    def _get_args(self, action: CleanAction) -> dict[str, Any]:
        args = super()._get_args(action)
        if action == CleanAction.START:
            args["content"].update(self._additional_content)
        return args


class GetCleanInfo(JsonCommandWithMessageHandling, MessageBodyDataDict):
    """Get clean info command."""

    NAME = "getCleanInfo"

    @classmethod
    def _handle_body_data_dict(
        cls, event_bus: EventBus, data: dict[str, Any]
    ) -> HandlingResult:
        """Handle message->body->data and notify the correct event subscribers.

        :return: A message response
        """
        status: State | None = None
        state = data.get("state")
        if data.get("trigger") == "alert":
            status = State.ERROR
        elif state in ("clean", "washing"):
            clean_state = data.get("cleanState", {})
            motion_state = clean_state.get("motionState")
            if motion_state == "working":
                status = State.CLEANING
            elif motion_state == "pause":
                status = State.PAUSED
            elif motion_state == "goCharging":
                status = State.RETURNING

            clean_type = clean_state.get("type")
            content = clean_state.get("content", {})
            if "type" in content:
                clean_type = content.get("type")

            if clean_type == "customArea":
                area_values = content
                if "value" in content:
                    area_values = content.get("value")

                _LOGGER.debug("Last custom area values (x1,y1,x2,y2): %s", area_values)

        elif state == "goCharging":
            status = State.RETURNING
        elif state == "idle":
            status = State.IDLE

        if status:
            event_bus.notify(StateEvent(status))
            return HandlingResult.success()

        return HandlingResult.analyse()


class GetCleanInfoV2(GetCleanInfo):
    """Get clean info v2 command."""

    NAME = "getCleanInfo_V2"


class NgiotClean(NgiotJsonCommand, ExecuteCommand):
    """Start, pause, resume or return to the dock through NGIOT."""

    NAME = "ngiotClean"
    APN = "40001"

    def __init__(self, action: CleanAction) -> None:
        self._action = action
        self.APN, args = self._request(action)
        super().__init__(args)

    @staticmethod
    def _request(action: CleanAction) -> tuple[str, dict[str, Any]]:
        requests: dict[CleanAction, tuple[str, dict[str, Any]]] = {
            CleanAction.START: ("40001", {"cleanSwitch": True, "cleanMode": "smart"}),
            CleanAction.PAUSE: ("40009", {"pauseSwitch": True}),
            CleanAction.RESUME: ("40011", {"pauseSwitch": False}),
            # A separate hard stop has not been verified on eyfj07.
            CleanAction.STOP: ("40013", {"chargeSwitch": True}),
        }
        return requests[action]

    async def _execute(
        self,
        authenticator: Authenticator,
        device_info: ApiDeviceInfo,
        event_bus: EventBus,
    ) -> tuple[HandlingResult, dict[str, Any]]:
        # Match the existing Clean command's start/resume behaviour, without
        # permanently replacing the caller's requested action.
        action = self._action
        state = event_bus.get_last_event(StateEvent)
        if state is not None:
            if action == CleanAction.START and state.state == State.PAUSED:
                action = CleanAction.RESUME
            elif action == CleanAction.RESUME and state.state != State.PAUSED:
                action = CleanAction.START

        self.APN, self._args = self._request(action)
        return await super()._execute(authenticator, device_info, event_bus)


class NgiotCleanArea(NgiotJsonCommand, ExecuteCommand):
    """Clean selected room IDs through NGIOT."""

    NAME = "ngiotCleanArea"
    APN = "40007"

    def __init__(
        self, mode: CleanMode, area: list[int | float], cleanings: int = 1
    ) -> None:
        if mode != CleanMode.SPOT_AREA:
            raise ValueError("NGIOT supports room-ID cleaning only")
        if cleanings != 1:
            raise ValueError("NGIOT room cleaning supports a single pass only")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in area):
            raise TypeError("NGIOT room IDs must be integers")
        if not area or any(value < 0 for value in area):
            raise ValueError("NGIOT requires non-empty, non-negative room IDs")
        super().__init__(
            {"cleanSwitch": True, "cleanMode": "area", "cleanValues": list(area)}
        )


class NgiotGetCleanInfo(NgiotJsonCommand, JsonGetCommand):
    """Get cleaning state from the NGIOT robot-detail snapshot."""

    NAME = "ngiotGetCleanInfo"
    APN = "10001"

    def __init__(self) -> None:
        super().__init__({"fields": ["workMode", "pauseSwitch", "chargeStatus"]})

    @classmethod
    def _handle_body_data_dict(
        cls, event_bus: EventBus, data: dict[str, Any]
    ) -> HandlingResult:
        work_mode = data.get("workMode")
        pause_switch = data.get("pauseSwitch")
        charge_status = data.get("chargeStatus")
        if not isinstance(work_mode, str):
            return HandlingResult.analyse()
        if not isinstance(pause_switch, bool) or not isinstance(charge_status, bool):
            return HandlingResult.analyse()

        match work_mode.lower():
            case "auto_pause" | "pause" | "paused":
                state = State.PAUSED
            case (
                "smart"
                | "smartclean"
                | "area"
                | "auto"
                | "customarea"
                | "custom_area"
                | "spotarea"
                | "spot_area"
            ):
                state = State.PAUSED if pause_switch else State.CLEANING
            case "gocharge" | "go_charge" | "gocharging" | "return_dock" | "returning":
                state = State.RETURNING
            case "stop" | "idle":
                state = State.DOCKED if charge_status else State.IDLE
            case _:
                return HandlingResult.analyse()

        event_bus.notify(StateEvent(state))
        return HandlingResult.success()
