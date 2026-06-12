from __future__ import annotations

from typing import Optional

from base_core.framework.subprocess.messages import Message
from base_core.framework.subprocess.worker import Worker
from base_core.framework.subprocess.shared_memory.shared_memory_base_messages import ItemAvailable
from app_apps.analysis.phase_control.subprocess.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.subprocess.domain.envelope_optimizer import EnvelopeOptimizer
from app_apps.analysis.phase_control.subprocess.messages import (
    CorrectionAvailable,
    Reset,
    SetEnvelopeConfig,
    SetPaused,
)
from app_apps.analysis.phase_control.subprocess.domain.mode import ControlMode
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer


class EnvelopeWorker(Worker):
    name = ControlMode.ENVELOPE.value
    bus_messages = (ItemAvailable, SetPaused, Reset, SetEnvelopeConfig)
    read_buffer_cls = (SharedSpectrumBuffer,)

    def __init__(self) -> None:
        super().__init__()
        self._paused = True  # inactive by default; phase_tracking starts active
        self._config = EnvelopeConfig()
        self._optimizer: Optional[EnvelopeOptimizer] = None
        self._buffer_id = "spectrometer"

    def start(self) -> None:
        self._optimizer = EnvelopeOptimizer(self._config)

    def handle(self, msg: Message) -> None:
        if isinstance(msg, ItemAvailable):
            if msg.buffer_id != self._buffer_id:
                return
            self._process_item(msg)
        elif isinstance(msg, SetPaused):
            self._paused = msg.paused
            self.reply_ok(msg.request_id)
        elif isinstance(msg, Reset):
            self._optimizer.reset()
            self.reply_ok(msg.request_id)
        elif isinstance(msg, SetEnvelopeConfig):
            self._config = msg.config
            self._optimizer = EnvelopeOptimizer(msg.config)
            self.reply_ok(msg.request_id)

    def _process_item(self, msg: ItemAvailable) -> None:
        if self._paused:
            self.ack(msg.slot, msg.item_id, msg.buffer_id)
            return

        buf: SharedSpectrumBuffer = self._process_buffers[self._buffer_id]
        _, wavelengths, intensities = buf.read_spectrum_copy(msg.slot)
        result = self._optimizer.update(wavelengths, intensities)

        if result is not None:
            self.emit(CorrectionAvailable(angle=result.angle, sign=result.sign))

        self.ack(msg.slot, msg.item_id, msg.buffer_id)
