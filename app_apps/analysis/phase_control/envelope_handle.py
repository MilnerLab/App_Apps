from __future__ import annotations

from typing import TYPE_CHECKING

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from app_apps.analysis.phase_control.subprocess.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.subprocess.messages import SetEnvelopeConfig, SetPaused

if TYPE_CHECKING:
    from base_core.ipc.subprocess_service import SubprocessService


class EnvelopeHandle(BaseWorkerHandle):
    WORKER_ID = "envelope"

    def __init__(self, service: SubprocessService, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, service, bus)

    def set_config(self, config: EnvelopeConfig) -> None:
        self._request(SetEnvelopeConfig(config=config), self._on_set_config_reply)

    def set_paused(self, paused: bool) -> None:
        self._request(
            SetPaused(worker_id=self.WORKER_ID, paused=paused),
            self._on_set_paused_reply,
        )

    def _on_set_config_reply(self, reply: OKReply) -> None:
        pass

    def _on_set_paused_reply(self, reply: OKReply) -> None:
        pass
