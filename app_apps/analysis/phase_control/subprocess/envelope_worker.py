from __future__ import annotations

from typing import Optional

from base_core.framework.subprocess.messages import Message
from base_core.framework.subprocess.shared_memory.models import SharedRingBufferSpec
from base_core.framework.subprocess.worker import ConsumerWorker
from app_apps.analysis.phase_control.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.domain.envelope_signal_generator import EnvelopeSignalGenerator
from app_apps.analysis.phase_control.subprocess.subprocess_messages import (
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
        self._generator: Optional[EnvelopeSignalGenerator] = None

    def start(self) -> None:
        self._generator = EnvelopeSignalGenerator(self._config)

    def attach_buffer(self, spec: SharedRingBufferSpec) -> SharedSpectrumBuffer:
        return SharedSpectrumBuffer.attach(spec)

    def handle(self, msg: Message, request_id: Optional[str]) -> None:
        if isinstance(msg, SetPaused):
            self._paused = msg.paused
            self.reply_ok(request_id)
        elif isinstance(msg, Reset):
            self._generator.reset()
            self.reply_ok(request_id)
        elif isinstance(msg, SetEnvelopeConfig):
            self._config = msg.config
            self._generator = EnvelopeSignalGenerator(msg.config)
            self.reply_ok(request_id)
        else:
            super().handle(msg, request_id)

    def on_item(self, slot: int, item_id: int, timestamp_ns: int) -> None:  # noqa: ARG002
        if self._paused:
            self.ack(slot, item_id)
            return

        _, wavelengths, intensities = self.buffer.read_spectrum_copy(slot)
        result = self._generator.update(wavelengths, intensities)

        if result is not None:
            self.emit(CorrectionAvailable(
                correction_deg=float(result.angle.Deg),
                phase_deg=0.0,
                sign=result.sign,
            ))

        self.ack(slot, item_id)
