from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.messages import CorrectionAvailable, SetPaused, SetStabilizationConfig
from app_apps.io.control_readout.events import RequestRotate

class PhaseTrackingHandle(BaseWorkerHandle):
    WORKER_ID = "phase_tracking"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus)

    def _on_attached(self) -> None:
        self._subscribe_service(CorrectionAvailable, self._on_correction_available)

    def _on_correction_available(self, msg: CorrectionAvailable) -> None:
        self._bus.publish(RequestRotate(angle=msg.angle, sign=msg.sign))

    def set_config(self, config: StabilizationConfig) -> None:
        self._request(SetStabilizationConfig(config=config), self._on_set_config_reply)

    def set_paused(self, paused: bool) -> None:
        self._request(
            SetPaused(worker_id=self.WORKER_ID, paused=paused),
            self._on_set_paused_reply,
        )

    def _on_set_config_reply(self, reply: OKReply) -> None:
        pass

    def _on_set_paused_reply(self, reply: OKReply) -> None:
        pass
