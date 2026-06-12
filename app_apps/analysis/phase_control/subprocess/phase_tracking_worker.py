from __future__ import annotations

from typing import Optional

from base_core.framework.subprocess.messages import Message
from base_core.framework.subprocess.worker import Worker
from base_core.framework.subprocess.shared_memory.shared_memory_base_messages import ItemAvailable
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_corrector import PhaseCorrector
from app_apps.analysis.phase_control.subprocess.domain.phase_tracker import PhaseTracker
from app_apps.analysis.phase_control.subprocess.messages import (
    ConfigSynced,
    CorrectionAvailable,
    Reset,
    SetStabilizationConfig,
    SetPaused,
)
from app_apps.analysis.phase_control.subprocess.domain.mode import ControlMode
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer


class PhaseTrackingWorker(Worker):
    name = ControlMode.PHASE_TRACKING.value
    bus_messages = (ItemAvailable, SetPaused, Reset, SetStabilizationConfig)
    read_buffer_cls = (SharedSpectrumBuffer,)

    def __init__(self) -> None:
        super().__init__()
        self._paused = False
        self._config = StabilizationConfig()
        self._tracker: Optional[PhaseTracker] = None
        self._corrector: Optional[PhaseCorrector] = None
        self._buffer_id = "spectrometer"

    def start(self) -> None:
        self._tracker = PhaseTracker(self._config)
        self._corrector = PhaseCorrector()

    def handle(self, msg: Message) -> None:
        if isinstance(msg, ItemAvailable):
            if msg.buffer_id != self._buffer_id:
                return
            self._process_item(msg)
        elif isinstance(msg, SetPaused):
            self._paused = msg.paused
            self.reply_ok(msg.request_id)
        elif isinstance(msg, Reset):
            self._tracker = PhaseTracker(self._config)
            self._corrector = PhaseCorrector()
            self.reply_ok(msg.request_id)
        elif isinstance(msg, SetStabilizationConfig):
            self._config = msg.config
            self._tracker = PhaseTracker(msg.config)
            self._corrector = PhaseCorrector()
            self.reply_ok(msg.request_id)

    def _process_item(self, msg: ItemAvailable) -> None:
        if self._paused:
            self.ack(msg.slot, msg.item_id, msg.buffer_id)
            return

        buf: SharedSpectrumBuffer = self._process_buffers[self._buffer_id]
        _, wavelengths, intensities = buf.read_spectrum_copy(msg.slot)
        self._tracker.update(wavelengths, intensities)

        if self._tracker.current_phase is not None:
            self.emit(ConfigSynced(config=self._config))
            result = self._corrector.update(self._tracker.current_phase)
            if result is not None:
                self.emit(CorrectionAvailable(angle=result.angle, sign=result.sign))

        self.ack(msg.slot, msg.item_id, msg.buffer_id)
