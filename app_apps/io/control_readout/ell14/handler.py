from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import Reply
from base_core.math.models import Angle
from control_readout.ell14.messages import (
    CurrentELL14Position,
    ELL14PositionReply,
    GetCurrentELL14Position,
    HomeELL14Rotator,
    RotateELL14,
)

from app_apps.io.control_readout.ell14.events import (
    ELL14RotatorHomed,
    ELL14WorkerStateChanged,
    NewELL14Angle,
    RequestRotate,
)
from app_apps.io.control_readout.motorized_stage_handle import MotorizedStageHandle


class ELL14RotatorHandle(MotorizedStageHandle):
    """
    Main-process handle to RotatorWorker.

    Bridges RequestRotate events from the main bus to Rotate IPC requests
    sent to the subprocess. The PhaseTrackingHandle publishes RequestRotate;
    this handle receives it and commands the physical rotator.
    """

    WORKER_ID = "rotator"
    REQUEST_MOVE_EVENT = RequestRotate
    POS_UPDATE_MSG = CurrentELL14Position

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=ELL14WorkerStateChanged)

    def _build_move_msg(self, value: Angle) -> RotateELL14:
        return RotateELL14(angle=value)

    def _build_home_msg(self) -> HomeELL14Rotator:
        return HomeELL14Rotator()

    def _build_get_pos_msg(self) -> GetCurrentELL14Position:
        return GetCurrentELL14Position()

    def _move_value_from_event(self, event: RequestRotate) -> Angle:
        return event.angle

    def _msg_value(self, msg: CurrentELL14Position | ELL14PositionReply) -> Angle:
        return msg.angle

    def _build_position_event(self, value: Angle) -> NewELL14Angle:
        return NewELL14Angle(value)

    def _on_home_reply(self, reply: Reply) -> None:
        self._bus.publish(ELL14RotatorHomed())
