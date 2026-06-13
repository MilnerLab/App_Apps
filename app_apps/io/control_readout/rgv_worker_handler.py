from __future__ import annotations

from typing import TYPE_CHECKING

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from base_core.math.models import Angle
from control_readout.rgv100bl.messages import HomeHwp, RotateHwpTo

from app_apps.io.control_readout.command_events import RequestRotateHwp

if TYPE_CHECKING:
    from base_core.ipc.subprocess_service import SubprocessService


class RgvHandle(BaseWorkerHandle):
    """Main-process handle to the RGV100BL HWP rotator."""

    WORKER_ID = "rgv100bl"

    def __init__(self, service: "SubprocessService", bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, service, bus)

    def _on_attached(self) -> None:
        self._subscribe(RequestRotateHwp, self._on_request_rotate)

    def rotate_to(self, angle: Angle) -> None:
        self._request(RotateHwpTo(angle=angle), self._on_reply)

    def home(self) -> None:
        self._request(HomeHwp(), self._on_reply)

    def _on_request_rotate(self, event: RequestRotateHwp) -> None:
        self._request(RotateHwpTo(angle=event.angle), self._on_reply)

    def _on_reply(self, reply: OKReply) -> None:
        pass
