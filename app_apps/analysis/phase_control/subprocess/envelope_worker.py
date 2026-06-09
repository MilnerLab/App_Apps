from __future__ import annotations

from typing import Optional

from base_core.framework.subprocess.messages import Message
from base_core.framework.subprocess.shared_memory.models import SharedRingBufferSpec
from base_core.framework.subprocess.worker import ConsumerWorker
from app_apps.analysis.phase_control.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.domain.envelope_optimizer import EnvelopeOptimizer
from app_apps.analysis.phase_control.subprocess.messages import (
    CorrectionAvailable,
    Reset,
    SetEnvelopeConfig,
    SetPaused,
)
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer


class EnvelopeWorker(ConsumerWorker[SharedSpectrumBuffer]):
    name = "envelope"

    def __init__(self) -> None:
        super().__init__(buffer_id="spectrometer")
        self._paused = True  # inactive by default; phase_tracking starts active
        self._config = EnvelopeConfig()
        self._optimizer: Optional[EnvelopeOptimizer] = None

    def start(self) -> None:
        self._optimizer = EnvelopeOptimizer(self._config)

    def attach_buffer(self, spec: SharedRingBufferSpec) -> SharedSpectrumBuffer:
        return SharedSpectrumBuffer.attach(spec)

    def handle(self, msg: Message, request_id: Optional[str]) -> None:
        if isinstance(msg, SetPaused):
            self._paused = msg.paused
            self.reply_ok(request_id)
        elif isinstance(msg, Reset):
            self._optimizer.reset()
            self.reply_ok(request_id)
        elif isinstance(msg, SetEnvelopeConfig):
            self._config = msg.config
            self._optimizer = EnvelopeOptimizer(msg.config)
            self.reply_ok(request_id)
        else:
            super().handle(msg, request_id)

    def on_item(self, slot: int, item_id: int, timestamp_ns: int) -> None:  # noqa: ARG002
        if self._paused:
            self.ack(slot, item_id)
            return

        _, wavelengths, intensities = self.buffer.read_spectrum_copy(slot)
        result = self._optimizer.update(wavelengths, intensities)

        if result is not None:
            self.emit(CorrectionAvailable(
                correction=result
            ))

        self.ack(slot, item_id)
