from __future__ import annotations

import logging

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from base_core.math.models import Angle
from control_readout.newport_xps.rgv100bl.messages import GetCurrentRGVAngle, HomeRGV, RGVAngleReply, RotateRGVTo

from app_apps.io.control_readout.rgv.events import (
    NewRGVAngle,
    RequestCurrentRGVAngle,
    RequestRotateRGV,
    RgvWorkerStateChanged,
)

log = logging.getLogger(__name__)


class RgvHandle(BaseWorkerHandle):
    """Main-process handle to the RGV100BL HWP rotator."""

    WORKER_ID = "rgv100bl"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=RgvWorkerStateChanged)

    def subscribe(self) -> None:
        self._subscribe(RequestRotateRGV, self._on_request_rotate)
        self._subscribe(RequestCurrentRGVAngle, self._on_request_current_angle)

    def home(self) -> None:
        log.info("RgvHandle: requesting HomeRGV")
        self._request(HomeRGV(), self._on_rotate_reply)

    def _on_request_rotate(self, event: RequestRotateRGV) -> None:
        log.info(
            "RgvHandle: RequestRotateRGV angle=%.4f deg -> sending RotateRGVTo to subprocess",
            event.angle.Deg,
        )
        self._request(RotateRGVTo(angle=event.angle), self._on_rotate_reply)

    def _on_request_current_angle(self, event: RequestCurrentRGVAngle) -> None:
        self._request(GetCurrentRGVAngle(), self._on_angle_reply)

    def _on_rotate_reply(self, reply: OKReply) -> None:
        log.info("RgvHandle: RGV move acknowledged (OKReply)")

    def _on_angle_reply(self, reply: RGVAngleReply) -> None:
        self._bus.publish(NewRGVAngle(angle=reply.angle))