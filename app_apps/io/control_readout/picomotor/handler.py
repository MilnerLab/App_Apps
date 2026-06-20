from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from control_readout.picomotor.messages import StepBy

from app_apps.io.control_readout.picomotor.events import RequestStepPicomotor


class PicomotorHandle(BaseWorkerHandle):
    """Main-process handle to the mirror picomotors (manual)."""

    WORKER_ID = "picomotor"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus)

    def subscribe(self) -> None:
        self._subscribe(RequestStepPicomotor, self._on_request_step)

    def step(self, axis: int, steps: int) -> None:
        self._request(StepBy(axis=axis, steps=steps), self._on_reply)

    def _on_request_step(self, event: RequestStepPicomotor) -> None:
        self._request(StepBy(axis=event.axis, steps=event.steps), self._on_reply)

    def _on_reply(self, reply: OKReply) -> None:
        pass
