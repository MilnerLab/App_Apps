from __future__ import annotations

from typing import TYPE_CHECKING

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from oscilloscope.config import ScopeConfig
from oscilloscope.messages import SetScopeConfig

if TYPE_CHECKING:
    from base_core.ipc.subprocess_service import SubprocessService


class OscilloscopeWorkerHandle(BaseWorkerHandle):
    """Main-process handle to the OscilloscopeWorker (config push)."""

    WORKER_ID = "oscilloscope"

    def __init__(self, service: "SubprocessService", bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, service, bus)

    def set_config(self, config: ScopeConfig) -> None:
        self._request(SetScopeConfig(config=config), self._on_reply)

    def _on_reply(self, reply: OKReply) -> None:
        pass
