from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from app_apps.analysis.phase_control.subprocess.domain.mode import ControlMode
from app_apps.io.spectrometer.events import SpectrumAvailable, SpectrumAck
from base_core.framework.events import EventBus
from base_qt.ui.buffer_consumer_mixin import BufferOutput
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.buffer_consumer_mixin import BufferConsumerMixin
from base_qt.ui.panel_vm import PanelVM, ui_thread
from spm_002.buffer import SpectrumBuffer
from app_apps.analysis.phase_control.subprocess.messages import CorrectionAvailable
from app_apps.analysis.phase_control.service import PhaseControlService


class PhaseControlVM(BufferConsumerMixin, PanelVM):
    CONSUMER_ID: ClassVar[str] = "phase_control_vm"

    correction_updated = Signal(float)  # correction angle in degrees
    running_changed    = Signal(bool)
    paused_changed     = Signal(bool)
    spectrum_updated   = Signal(object, object)  # (wavelengths: ndarray, intensities: ndarray)

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        phase_control_svc: PhaseControlService,
        spec_output: BufferOutput,
        spec_buffer: SpectrumBuffer,
    ) -> None:
        PanelVM.__init__(self, bus, dispatcher)
        self._setup_consumer(spec_output)
        self._svc         = phase_control_svc
        self._spec_buffer = spec_buffer
        self._paused  = False
        self._running = phase_control_svc.is_running
        self._sub(SpectrumAvailable, self._on_spectrum)
        self._sub(CorrectionAvailable, self._on_correction_available)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @ui_thread
    def _on_correction_available(self, event: CorrectionAvailable) -> None:
        self.correction_updated.emit(float(event.angle.Deg))

    @ui_thread
    def _on_spectrum(self, event: SpectrumAvailable) -> None:
        try:
            _, wavelengths, intensities = self._spec_buffer.read_spectrum_copy(event.slot)
            self.spectrum_updated.emit(wavelengths, intensities)
        except Exception as exc:
            self._msg(f"Spectrum read error: {exc}", MessageLevel.WARNING)
        finally:
            self._bus.publish(SpectrumAck(slot=event.slot, item_id=event.item_id, consumer_id=self.CONSUMER_ID))

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def start_worker(self) -> None:
        """Start the service, or resume if currently paused."""
        if self._paused:
            self._paused = False
            self._svc.set_worker_paused(False)
            self.paused_changed.emit(False)
        

    def pause(self) -> None:
        self._paused = True
        self._svc.set_worker_paused(True)
        self.paused_changed.emit(True)

    def reset(self) -> None:
        """Reset algorithm state and stop the service."""
        self._svc.reset_worker()

    def set_mode(self, mode: ControlMode) -> None:
        self._svc.set_mode(mode)

    def open_config(self, parent: QWidget) -> None:
        from app_apps.analysis.phase_control.ui.phase_config_dialog import PhaseConfigDialog
        if not hasattr(self, "_config_dialog"):
            self._config_dialog = PhaseConfigDialog(self._svc, parent=parent)
        self._config_dialog.open()
