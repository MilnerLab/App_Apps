from __future__ import annotations

from typing import Optional

from base_core.framework.subprocess.messages import Message
from base_core.framework.subprocess.shared_memory.models import SharedRingBufferSpec
from base_core.framework.subprocess.worker import ConsumerWorker
from app_apps.analysis.phase_control.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.domain.phase_corrector import PhaseCorrector
from app_apps.analysis.phase_control.domain.phase_tracker import PhaseTracker
from app_apps.analysis.phase_control.subprocess.messages import (
    ConfigSynced,
    CorrectionAvailable,
    Reset,
    SetStabilizationConfig,
    SetPaused,
)
from app_apps.analysis.phase_control.domain.mode import ControlMode
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer


class PhaseTrackingWorker(ConsumerWorker[SharedSpectrumBuffer]):
    name = ControlMode.PHASE_TRACKING.value

    def __init__(self) -> None:
        super().__init__(buffer_id="spectrometer")
        self._paused = False
        self._config = StabilizationConfig()
        self._tracker: Optional[PhaseTracker] = None
        self._corrector: Optional[PhaseCorrector] = None

    def start(self) -> None:
        self._tracker = PhaseTracker(self._config)
        self._corrector = PhaseCorrector()

    def attach_buffer(self, spec: SharedRingBufferSpec) -> SharedSpectrumBuffer:
        return SharedSpectrumBuffer.attach(spec)

    def handle(self, msg: Message, request_id: Optional[str]) -> None:
        if isinstance(msg, SetPaused):
            self._paused = msg.paused
            self.reply_ok(request_id)
        elif isinstance(msg, Reset):
            self._tracker = PhaseTracker(self._config)
            self._corrector = PhaseCorrector()
            self.reply_ok(request_id)
        elif isinstance(msg, SetStabilizationConfig):
            self._config = msg.config
            self._tracker = PhaseTracker(msg.config)
            self._corrector = PhaseCorrector()
            self.reply_ok(request_id)
        else:
            super().handle(msg, request_id)

    def on_item(self, slot: int, item_id: int, timestamp_ns: int) -> None:  # noqa: ARG002
        if self._paused:
            self.ack(slot, item_id)
            return

        _, wavelengths, intensities = self.buffer.read_spectrum_copy(slot)
        self._tracker.update(wavelengths, intensities)

        if self._tracker.current_phase is not None:
            self.emit(ConfigSynced(config=self._config))
            result = self._corrector.update(self._tracker.current_phase)
            if result is not None:
                self.emit(CorrectionAvailable(angle=result.angle, sign=result.sign))


        self.ack(slot, item_id)
