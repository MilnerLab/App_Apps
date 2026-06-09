from __future__ import annotations

from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from base_core.framework.subprocess.subprocess_service import SubprocessService
from base_core.framework.subprocess.shared_memory.shared_buffer_coordinator import (
    SharedBufferCoordinator,
)
from base_core.framework.subprocess.worker_handle import WorkerHandle
from app_apps.analysis.phase_control.domain.analysis_mode import AnalysisMode
from app_apps.analysis.phase_control.domain.config import AnalysisConfig
from app_apps.analysis.phase_control.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.subprocess.subprocess_messages import (
    ConfigSynced,
    Reset,
    SetAnalysisConfig,
    SetEnvelopeConfig,
    SetPaused,
)
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer

_MODE_WORKER = {
    AnalysisMode.PHASE_TRACKING: "phase_tracking",
    AnalysisMode.ENVELOPE: "envelope",
}


class PhaseControlService(SubprocessService):
    def __init__(
        self,
        io: TaskRunner,
        endpoint: JsonlSubprocessEndpoint,
        bus: EventBus,
        spec_buffer: SharedSpectrumBuffer,
        spec_coordinator: SharedBufferCoordinator,
        config: AnalysisConfig,
    ) -> None:
        super().__init__(io=io, endpoint=endpoint, bus=bus)
        self._config = config
        self._active = AnalysisMode.PHASE_TRACKING
        for worker_name in ("phase_tracking", "envelope"):
            handle = (
                WorkerHandle(service=self, name=worker_name, bus=bus)
                .with_input("spectrometer", spec_coordinator, spec_buffer)
            )
            self._register_handle(worker_name, handle)

    def start(self) -> None:
        super().start()
        self._unsub_config = self._bus.subscribe(
            ConfigSynced, self._on_config_synced, source="phase_control"
        )
        self.worker("phase_tracking").start_async(key="phase_tracking.start")
        self.worker("envelope").start_async(key="envelope.start")

    def stop(self) -> None:
        self._unsub_config()
        self.worker("phase_tracking").stop()
        self.worker("envelope").stop()
        super().stop()

    def _on_config_synced(self, event: ConfigSynced) -> None:
        self._config.copy_from(event.config)

    # ------------------------------------------------------------------
    # Runtime control
    # ------------------------------------------------------------------

    def set_active(self, mode: AnalysisMode) -> None:
        """Switch the active analysis mode. The inactive worker acks slots without processing."""
        if mode == self._active:
            return
        self.worker(_MODE_WORKER[self._active]).send(SetPaused(paused=True))
        self.worker(_MODE_WORKER[mode]).send(SetPaused(paused=False))
        self._active = mode

    def set_paused(self, paused: bool) -> None:
        """Pause/resume the active worker. Pausing keeps the worker running so the ring buffer drains."""
        self.worker(_MODE_WORKER[self._active]).send(SetPaused(paused=paused))

    def reset(self) -> None:
        """Reset the active worker's algorithm state without restarting the subprocess."""
        self.worker(_MODE_WORKER[self._active]).send(Reset())

    def set_config(self) -> None:
        """Push the current container config to the phase tracking worker."""
        self.worker("phase_tracking").send(SetAnalysisConfig(config=self._config))

    def set_envelope_config(self, config: EnvelopeConfig) -> None:
        self.worker("envelope").send(SetEnvelopeConfig(config=config))
