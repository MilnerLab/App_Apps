from __future__ import annotations

from typing import ClassVar

from base_core.framework.app.app_message import AppMessage, MessageLevel
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from base_core.framework.subprocess.subprocess_service import SubprocessService
from base_core.framework.subprocess.shared_memory.shared_buffer_coordinator import (
    SharedBufferCoordinator,
)
from base_core.framework.subprocess.worker_handle import WorkerHandle
from app_apps.analysis.phase_control.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.domain.mode import ControlMode
from app_apps.analysis.phase_control.subprocess.messages import (
    ConfigSynced,
    Reset,
    SetStabilizationConfig,
    SetEnvelopeConfig,
    SetPaused,
)
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer

_MODE_WORKER = {
    ControlMode.PHASE_TRACKING: "phase_tracking",
    ControlMode.ENVELOPE: "envelope",
}


class PhaseControlService(SubprocessService):
    service_name: ClassVar[str] = "phase_control"

    def __init__(
        self,
        io: TaskRunner,
        endpoint: JsonlSubprocessEndpoint,
        bus: EventBus,
        spec_buffer: SharedSpectrumBuffer,
        spec_coordinator: SharedBufferCoordinator,
        config: StabilizationConfig,
    ) -> None:
        super().__init__(io=io, endpoint=endpoint, bus=bus)
        self._config = config
        self._active = ControlMode.PHASE_TRACKING
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
        def _publish_err(exc: BaseException) -> None:
            self._bus.publish(AppMessage(f"Phase control failed to start: {exc}", MessageLevel.ERROR))

        self.worker("phase_tracking").start_async(key="phase_tracking.start", on_error=_publish_err)
        self.worker("envelope").start_async(key="envelope.start", on_error=_publish_err)
        self._publish_status(True)

    def stop(self) -> None:
        self._publish_status(False)
        self._unsub_config()
        self.worker("phase_tracking").stop()
        self.worker("envelope").stop()
        super().stop()

    def _on_config_synced(self, event: ConfigSynced) -> None:
        self._config.copy_from(event.config)

    # ------------------------------------------------------------------
    # Runtime control
    # ------------------------------------------------------------------

    def set_active(self, mode: ControlMode) -> None:
        """Switch the active control mode. The inactive worker acks slots without processing."""
        if mode == self._active:
            return
        self.worker(_MODE_WORKER[self._active]).send(SetPaused(paused=True))
        self.worker(_MODE_WORKER[mode]).send(SetPaused(paused=False))
        self._active = mode

    def set_paused(self, paused: bool) -> None:
        """Pause/resume the active worker. Pausing keeps the worker running so the ring buffer drains."""
        self.worker(_MODE_WORKER[self._active]).send(SetPaused(paused=paused))
        self._publish_status(not paused, "paused" if paused else "")

    def reset(self) -> None:
        """Reset the active worker's algorithm state without restarting the subprocess."""
        self.worker(_MODE_WORKER[self._active]).send(Reset())

    def set_config(self) -> None:
        """Push the current container config to the phase tracking worker."""
        self.worker("phase_tracking").send(SetStabilizationConfig(config=self._config))

    def set_envelope_config(self, config: EnvelopeConfig) -> None:
        self.worker("envelope").send(SetEnvelopeConfig(config=config))
