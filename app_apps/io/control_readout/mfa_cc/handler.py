from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from control_readout.esp_301.mfa_cc.messages import (
    GetCurrentPosMFACC,
    HomeMFACC,
    MFACCPosReply,
    MFACCPosUpdate,
    MoveMFACCTo,
)

from app_apps.io.control_readout.mfa_cc.events import (
    MfaccWorkerStateChanged,
    NewMfaccPosition,
    RequestMoveMfacc,
)
from app_apps.io.control_readout.motorized_stage_handle import MotorizedStageHandle


class MfaccHandle(MotorizedStageHandle):
    """Main-process handle to the MFA-CC ESP301 linear stage."""

    WORKER_ID = "mfacc"
    REQUEST_MOVE_EVENT = RequestMoveMfacc
    POS_UPDATE_MSG = MFACCPosUpdate

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=MfaccWorkerStateChanged)

    def _build_move_msg(self, value: float) -> MoveMFACCTo:
        return MoveMFACCTo(position=value)

    def _build_home_msg(self) -> HomeMFACC:
        return HomeMFACC()

    def _build_get_pos_msg(self) -> GetCurrentPosMFACC:
        return GetCurrentPosMFACC()

    def _move_value_from_event(self, event: RequestMoveMfacc) -> float:
        return event.position

    def _msg_value(self, msg: MFACCPosUpdate | MFACCPosReply) -> float:
        return msg.position

    def _build_position_event(self, value: float) -> NewMfaccPosition:
        return NewMfaccPosition(position=value)
