from __future__ import annotations

from typing import ClassVar

from base_core.framework.app.app_message import AppMessage, MessageLevel
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from base_core.framework.subprocess.subprocess_service import SubprocessService
from base_core.framework.subprocess.shared_memory.buffer_output import BufferOutput
from base_core.framework.subprocess.worker_handle import WorkerHandle
from app_apps.analysis.phase_control.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.domain.mode import ControlMode
from app_apps.analysis.phase_control.subprocess.messages import (
    ConfigSynced,
    CorrectionAvailable,
    Reset,
    SetStabilizationConfig,
    SetEnvelopeConfig,
    SetPaused,
)
from app_apps.io.control_readout.events import RotateRequested
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer


class PhaseControlService(SubprocessService):
    service_name: ClassVar[str] = "phase_control"

    def __init__(
        self,
        io: TaskRunner,
        endpoint: JsonlSubprocessEndpoint,
        bus: EventBus,
        spec_buffer: SharedSpectrumBuffer,
        spec_output: BufferOutput,
        config: StabilizationConfig,
    ) -> None:
        super().__init__(io=io, endpoint=endpoint, bus=bus)
        self._config = config
        self._current_mode = ControlMode.PHASE_TRACKING
        for mode in ControlMode:
            handle = (
                WorkerHandle(service=self, name=mode.value, bus=bus)
                .with_input("spectrometer", spec_output.coordinator, spec_buffer)
            )
            self._register_handle(mode.value, handle)

    def start(self) -> None:
        super().start()
        self._unsub_config = self._bus.subscribe(
            ConfigSynced, self._on_config_synced, source="phase_control"
        )
        self._unsub_rotate = self._bus.subscribe(
            CorrectionAvailable, self._on_request_rotate, source="phase_control"
        )
        def _publish_err(exc: BaseException) -> None:
            self._bus.publish(AppMessage(f"Phase control failed to start: {exc}", MessageLevel.ERROR))

        for mode in ControlMode:
            self.worker(mode.value).start_async(key=f"{mode.value}.start", on_error=_publish_err)
        self._publish_status(True)

    def stop(self) -> None:
        self._publish_status(False)
        self._unsub_config()
        self._unsub_rotate()
        for mode in ControlMode:
            self.worker(mode.value).stop()
        super().stop()

    def _on_config_synced(self, event: ConfigSynced) -> None:
        self._config.copy_from(event.config)

    def _on_request_rotate(self, event: CorrectionAvailable) -> None:
        self._bus.publish(RotateRequested(angle=event.angle, sign=event.sign))

    # ------------------------------------------------------------------
    # Runtime control
    # ------------------------------------------------------------------

    def set_mode(self, mode: ControlMode) -> None:
        """Switch the active control mode. The inactive worker acks slots without processing."""
        if mode == self._current_mode:
            return
        self.worker(self._current_mode.value).send(SetPaused(paused=True))
        self._current_mode = mode

    def set_worker_paused(self, paused: bool) -> None:
        """Pause/resume the active worker. Pausing keeps the worker running so the ring buffer drains."""
        self.worker(self._current_mode.value).send(SetPaused(paused=paused))

    def reset_worker(self) -> None:
        """Reset the active worker's algorithm state without restarting the subprocess."""
        self.worker(self._current_mode.value).send(Reset())

    def set_config(self) -> None:
        """Push the current container config to the phase tracking worker."""
        self.worker(ControlMode.PHASE_TRACKING.value).send(SetStabilizationConfig(config=self._config))

    def set_envelope_config(self, config: EnvelopeConfig) -> None:
        self.worker(ControlMode.ENVELOPE.value).send(SetEnvelopeConfig(config=config))
