from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import Signal

from app_apps.io.spectrometer.events import SpectrumAvailable, SpectrumAck
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle
from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_vm import PanelVM, ui_thread
from app_apps.analysis.phase_control.service import PhaseControlService


class PhaseControlVM(PanelVM):
    CONSUMER_ID: ClassVar[str] = "phase_control_vm"

    spectrum_updated = Signal(object, object)  # (wavelengths: ndarray, intensities: ndarray)

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        phase_control_svc: PhaseControlService,
        spec_handle: SpectrometerWorkerHandle,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._spec_handle = spec_handle
        self._svc = phase_control_svc
        spec_handle.register_consumer(self.CONSUMER_ID)
        self._sub(SpectrumAvailable, self._on_spectrum)

    def on_close(self) -> None:
        self._spec_handle.unregister_consumer(self.CONSUMER_ID)
        super().on_close()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @ui_thread
    def _on_spectrum(self, event: SpectrumAvailable) -> None:
        try:
            buf = self._spec_handle.buffer
            wavelengths = buf.wavelengths(event.slot)
            intensities = buf.intensities(event.slot)
            self.spectrum_updated.emit(wavelengths, intensities)
        except Exception as exc:
            self._msg(f"Spectrum read error: {exc}", MessageLevel.WARNING)
        finally:
            self._bus.publish(SpectrumAck(slot=event.slot, item_id=event.item_id, consumer_id=self.CONSUMER_ID))
