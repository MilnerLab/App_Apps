from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.buffer_consumer_mixin import BufferConsumerMixin
from base_qt.ui.panel_vm import PanelVM, ui_thread
from base_qt.ui.app_message import MessageLevel

from app_apps.io.spectrometer.events import SpectrumAvailable, SpectrumAck
from app_apps.io.spectrometer.service import SpectrometerService, WORKER_NAME as SPECTROMETER_WORKER
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer


class SpectrumVM(BufferConsumerMixin, PanelVM):
    CONSUMER_ID: ClassVar[str] = "spectrum_vm"

    spectrum_updated = Signal(object, object)  # (wavelengths: ndarray, intensities: ndarray)

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        svc: SpectrometerService,
        buffer: SharedSpectrumBuffer,
    ) -> None:
        PanelVM.__init__(self, bus, dispatcher)
        self._setup_consumer(svc.output)
        self._svc    = svc
        self._buffer = buffer
        self._sub(SpectrumAvailable, self._on_spectrum)

    @ui_thread
    def _on_spectrum(self, event: SpectrumAvailable) -> None:
        if event.consumer_id != self.CONSUMER_ID:
            return
        try:
            _header, wavelengths, intensities = self._buffer.read_spectrum_copy(event.slot)
            self.spectrum_updated.emit(wavelengths, intensities)
        except Exception as exc:
            self._msg(f"Spectrum read error: {exc}", MessageLevel.WARNING)
        finally:
            self._bus.publish(SpectrumAck(slot=event.slot, item_id=event.item_id, consumer_id=self.CONSUMER_ID))

    def set_integration_time(self, ms: float) -> None:
        from spm_002.config import SpectrometerConfig
        from spm_002.messages import SetSpectrometerConfig
        self._svc.worker(SPECTROMETER_WORKER).request_async(
            SetSpectrometerConfig(config=SpectrometerConfig(integration_time_ms=ms)),
            key="spectrometer.set_integration_time",
        )
