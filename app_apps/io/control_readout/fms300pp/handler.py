from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from control_readout.esp_301.fms300pp.messages import (
    FMS300PPPosReply,
    FMS300PPPosUpdate,
    GetCurrentPosFMS300PP,
    HomeFMS300PP,
    MoveFMS300PPTo,
)

from app_apps.io.control_readout.fms300pp.events import (
    Fms300ppWorkerStateChanged,
    NewFms300ppPosition,
    RequestMoveFms300pp,
)
from app_apps.io.control_readout.motorized_stage_handle import MotorizedStageHandle


class Fms300ppHandle(MotorizedStageHandle):
    """Main-process handle to the FMS300PP ESP301 linear stage."""

    WORKER_ID = "fms300pp"
    REQUEST_MOVE_EVENT = RequestMoveFms300pp
    POS_UPDATE_MSG = FMS300PPPosUpdate

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=Fms300ppWorkerStateChanged)

    def _build_move_msg(self, value: float) -> MoveFMS300PPTo:
        return MoveFMS300PPTo(position=value)

    def _build_home_msg(self) -> HomeFMS300PP:
        return HomeFMS300PP()

    def _build_get_pos_msg(self) -> GetCurrentPosFMS300PP:
        return GetCurrentPosFMS300PP()

    def _move_value_from_event(self, event: RequestMoveFms300pp) -> float:
        return event.position

    def _msg_value(self, msg: FMS300PPPosUpdate | FMS300PPPosReply) -> float:
        return msg.position

    def _build_position_event(self, value: float) -> NewFms300ppPosition:
        return NewFms300ppPosition(position=value)
