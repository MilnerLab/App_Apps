from __future__ import annotations

from typing import ClassVar

from app_apps.analysis.phase_control.subprocess.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.subprocess.domain.mode import ControlMode
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from base_core.framework.app.app_message import AppMessage, MessageLevel
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from base_core.framework.subprocess.subprocess_service import SubprocessService
from base_core.framework.subprocess.shared_memory.buffer_output import BufferOutput
from base_core.framework.subprocess.shared_memory.shared_memory_base_messages import ItemAvailable
from base_core.framework.subprocess.worker_handle import WorkerHandle
from app_apps.analysis.phase_control.subprocess.messages import (
    ConfigSynced,
    CorrectionAvailable,
    Reset,
    SetStabilizationConfig,
    SetEnvelopeConfig,
    SetPaused,
)
from app_apps.io.control_readout.events import RotateRequested
from app_apps.io.spectrometer.events import SpectrumAvailable
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
        self._spec_output = spec_output
        self._current_mode = ControlMode.PHASE_TRACKING

        self.add_read_buffer("spectrometer", spec_buffer, spec_output)

        for mode in ControlMode:
            self._register_handle(mode.value, WorkerHandle(service=self, name=mode.value))

    def start(self) -> None:
        super().start()
        # Register each worker as a consumer of the spectrometer buffer so the
        # coordinator knows how many acks are required before re-granting a slot.
        for mode in ControlMode:
            self._spec_output.register_consumer(mode.value)

        self._unsub_config = self._internal_bus.subscribe(ConfigSynced, self._on_config_synced)
        self._unsub_rotate = self._internal_bus.subscribe(CorrectionAvailable, self._on_correction_available)
        self._unsub_spectrum = self._spec_output.subscribe_available(self._bus, self._on_spectrum_available)

        def _publish_err(exc: BaseException) -> None:
            self._bus.publish(AppMessage(f"Phase control failed to start: {exc}", MessageLevel.ERROR))

        for mode in ControlMode:
            self.worker(mode.value).start_async(key=f"{mode.value}.start", on_error=_publish_err)
        self._publish_status(True)

    def stop(self) -> None:
        self._publish_status(False)
        self._unsub_config()
        self._unsub_rotate()
        self._unsub_spectrum()
        for mode in ControlMode:
            self.worker(mode.value).stop()
        super().stop()

    def _on_config_synced(self, event: ConfigSynced) -> None:
        self._config.copy_from(event.config)

    def _on_correction_available(self, message: CorrectionAvailable) -> None:
        self._bus.publish(RotateRequested(angle_rad=float(message.angle.Rad)))
        self._bus.publish(message)

    def _on_spectrum_available(self, event: SpectrumAvailable) -> None:
        for mode in ControlMode:
            self.worker(mode.value).send(ItemAvailable(
                consumer_id=mode.value,
                slot=event.slot,
                item_id=event.item_id,
                timestamp_ns=event.timestamp_ns,
                buffer_id=self._spec_output.buffer_id,
            ))

    # ------------------------------------------------------------------
    # Runtime control
    # ------------------------------------------------------------------

    def set_mode(self, mode: ControlMode) -> None:
        if mode == self._current_mode:
            return
        self.worker(self._current_mode.value).send(SetPaused(paused=True))
        self._current_mode = mode

    def set_worker_paused(self, paused: bool) -> None:
        self.worker(self._current_mode.value).send(SetPaused(paused=paused))

    def reset_worker(self) -> None:
        self.worker(self._current_mode.value).send(Reset())

    def set_config(self) -> None:
        self.worker(ControlMode.PHASE_TRACKING.value).send(SetStabilizationConfig(config=self._config))

    def set_envelope_config(self, config: EnvelopeConfig) -> None:
        self.worker(ControlMode.ENVELOPE.value).send(SetEnvelopeConfig(config=config))
