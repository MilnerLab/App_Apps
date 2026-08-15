from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from control_readout.esp_301.uts150cc.messages import (
    GetCurrentPosUTS150CC,
    HomeUTS150CC,
    MoveUTS150CCTo,
    UTS150CCPosReply,
    UTS150CCPosUpdate,
)

from app_apps.io.control_readout.motorized_stage_handle import MotorizedStageHandle
from app_apps.io.control_readout.uts150cc.events import (
    NewUts150ccPosition,
    RequestMoveUts150cc,
    Uts150ccWorkerStateChanged,
)


class Uts150ccHandle(MotorizedStageHandle):
    """Main-process handle to the UTS150CC ESP301 linear stage."""

    WORKER_ID = "uts150cc"
    REQUEST_MOVE_EVENT = RequestMoveUts150cc
    POS_UPDATE_MSG = UTS150CCPosUpdate

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=Uts150ccWorkerStateChanged)

    def _build_move_msg(self, value: float) -> MoveUTS150CCTo:
        return MoveUTS150CCTo(position=value)

    def _build_home_msg(self) -> HomeUTS150CC:
        return HomeUTS150CC()

    def _build_get_pos_msg(self) -> GetCurrentPosUTS150CC:
        return GetCurrentPosUTS150CC()

    def _move_value_from_event(self, event: RequestMoveUts150cc) -> float:
        return event.position

    def _msg_value(self, msg: UTS150CCPosUpdate | UTS150CCPosReply) -> float:
        return msg.position

    def _build_position_event(self, value: float) -> NewUts150ccPosition:
        return NewUts150ccPosition(position=value)
