from __future__ import annotations

from typing import TYPE_CHECKING

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from app_apps.io.control_readout.events import RequestRotate
from control_readout.elliptec.messages import HomeRotator, Rotate

if TYPE_CHECKING:
    from base_core.ipc.subprocess_service import SubprocessService


class RotatorHandle(BaseWorkerHandle):
    """
    Main-process handle to RotatorWorker.

    Bridges RequestRotate events from the main bus to Rotate IPC requests
    sent to the subprocess. The PhaseControlService's translation publishes
    RequestRotate; this handle receives it and commands the physical rotator.
    """

    WORKER_ID = "rotator"

    def __init__(self, service: SubprocessService, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, service, bus)

    def _on_attached(self) -> None:
        self._subscribe(RequestRotate, self._on_request_rotate)

    def home(self) -> None:
        self._request(HomeRotator(), self._on_home_reply)

    def _on_request_rotate(self, event: RequestRotate) -> None:
        self._request(Rotate(angle=event.angle), self._on_rotate_reply)

    def _on_rotate_reply(self, reply: OKReply) -> None:
        pass

    def _on_home_reply(self, reply: OKReply) -> None:
        pass
