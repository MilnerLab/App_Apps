from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.math.models import Angle
from control_readout.newport_xps.rgv100bl.messages import (
    GetCurrentRGVAngle,
    HomeRGV,
    RGVAngleReply,
    RGVAngleUpdate,
    RotateRGVTo,
)

from app_apps.io.control_readout.motorized_stage_handle import MotorizedStageHandle
from app_apps.io.control_readout.rgv.events import (
    NewRGVAngle,
    RequestCurrentRGVAngle,
    RequestRotateRGV,
    RgvWorkerStateChanged,
)


class RgvHandle(MotorizedStageHandle):
    """Main-process handle to the RGV100BL HWP rotator."""

    WORKER_ID = "rgv100bl"
    REQUEST_MOVE_EVENT = RequestRotateRGV
    POS_UPDATE_MSG = RGVAngleUpdate

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=RgvWorkerStateChanged)

    def subscribe(self) -> None:
        super().subscribe()
        self._subscribe(RequestCurrentRGVAngle, self._on_request_current_angle)

    def _build_move_msg(self, value: Angle) -> RotateRGVTo:
        return RotateRGVTo(angle=value)

    def _build_home_msg(self) -> HomeRGV:
        return HomeRGV()

    def _build_get_pos_msg(self) -> GetCurrentRGVAngle:
        return GetCurrentRGVAngle()

    def _move_value_from_event(self, event: RequestRotateRGV) -> Angle:
        return event.angle

    def _msg_value(self, msg: RGVAngleUpdate | RGVAngleReply) -> Angle:
        return msg.angle

    def _build_position_event(self, value: Angle) -> NewRGVAngle:
        return NewRGVAngle(angle=value)

    def _on_request_current_angle(self, event: RequestCurrentRGVAngle) -> None:
        self.get_position()