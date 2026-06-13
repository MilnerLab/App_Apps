from __future__ import annotations

from typing import TYPE_CHECKING

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from control_readout.servo_shutter.messages import BlockArm, UnblockArm

from app_apps.io.control_readout.command_events import RequestSetArmBlocked

if TYPE_CHECKING:
    from base_core.ipc.subprocess_service import SubprocessService


class ServoShutterHandle(BaseWorkerHandle):
    """Main-process handle to the centrifuge-arm servo shutters (manual stub)."""

    WORKER_ID = "servo_shutter"

    def __init__(self, service: "SubprocessService", bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, service, bus)

    def _on_attached(self) -> None:
        self._subscribe(RequestSetArmBlocked, self._on_request)

    def block(self, arm: int) -> None:
        self._request(BlockArm(arm=arm), self._on_reply)

    def unblock(self, arm: int) -> None:
        self._request(UnblockArm(arm=arm), self._on_reply)

    def _on_request(self, event: RequestSetArmBlocked) -> None:
        msg = BlockArm(arm=event.arm) if event.blocked else UnblockArm(arm=event.arm)
        self._request(msg, self._on_reply)

    def _on_reply(self, reply: OKReply) -> None:
        pass
