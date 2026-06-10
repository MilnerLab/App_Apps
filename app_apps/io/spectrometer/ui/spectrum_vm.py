from __future__ import annotations

import numpy as np
from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_vm import PanelVM, ui_thread
from base_qt.ui.app_message import MessageLevel

from app_apps.io.spectrometer.events import SpectrumAvailable
from app_apps.io.spectrometer.service import SpectrometerService
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer


class SpectrumVM(PanelVM):
    spectrum_updated = Signal(object, object)  # (wavelengths: ndarray, intensities: ndarray)

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        svc: SpectrometerService,
        buffer: SharedSpectrumBuffer,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._svc    = svc
        self._buffer = buffer
        self._sub(SpectrumAvailable, self._on_spectrum)

    @ui_thread
    def _on_spectrum(self, event: SpectrumAvailable) -> None:
        try:
            _header, wavelengths, intensities = self._buffer.read_spectrum_copy(event.slot)
            self.spectrum_updated.emit(wavelengths, intensities)
        except Exception as exc:
            self._msg(f"Spectrum read error: {exc}", MessageLevel.WARNING)
        finally:
            self._svc.ack_slot(event.slot, event.item_id, "ui")

    def set_integration_time(self, ms: float) -> None:
        from spm_002.config import SpectrometerConfig
        from spm_002.messages import SetSpectrometerConfig
        self._svc.worker("spectrometer").request_async(
            SetSpectrometerConfig(config=SpectrometerConfig(integration_time_ms=ms)),
            key="spectrometer.set_integration_time",
        )
