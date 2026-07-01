from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from base_core.math.models import Angle
from control_readout.newport_xps.rgv100bl.messages import GetCurrentRGVAngle, HomeHwp, RGVAngleUpdate, RotateHwpTo, RotateRGVTo

from app_apps.io.control_readout.rgv.events import NewRGVAngle, RequestCurrentRGVAngle, RequestRotateRGV


class RgvHandle(BaseWorkerHandle):
    """Main-process handle to the RGV100BL HWP rotator."""

    WORKER_ID = "rgv100bl"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus)

    def subscribe(self) -> None:
        self._subscribe(RequestRotateRGV, self._on_request_rotate)
        self._subscribe(RequestCurrentRGVAngle, self._on_request_current_angle)

    def _on_request_rotate(self, event: RequestRotateRGV) -> None:
        self._request(RotateRGVTo(angle=event.angle), self._on_rotate_reply)
        
    def _on_request_current_angle(self, event: RequestCurrentRGVAngle) -> None:
        self._request(GetCurrentRGVAngle(), self._on_angle_reply)

    def _on_rotate_reply(self, reply: OKReply) -> None:
        pass

    def _on_angle_reply(self, reply: RGVAngleUpdate) -> None:
        self._bus.publish(NewRGVAngle(angle=reply.angle))