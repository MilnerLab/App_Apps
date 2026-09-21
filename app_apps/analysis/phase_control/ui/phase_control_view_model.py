from __future__ import annotations

from typing import ClassVar

import numpy as np
from PySide6.QtCore import Signal

from app_apps.analysis.phase_control.subprocess.domain.mode import ControlMode
from app_apps.io.spectrometer.events import SpectrumAvailable, SpectrumAck
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle
from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread
from app_apps.analysis.phase_control.service import PhaseControlService
from app_apps.analysis.phase_control.ui.stabilization_control_view_model import StabilizationControlViewModel
from app_apps.analysis.phase_control.ui.envelope_control_view_model import EnvelopeControlViewModel


class PhaseControlViewModel(PanelViewModel):
    CONSUMER_ID: ClassVar[str] = "phase_control_vm"

    spectrum_updated = Signal(object, object)  # (wavelengths: ndarray, intensities: ndarray)

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        phase_control_svc: PhaseControlService,
        spec_handle: SpectrometerWorkerHandle,
        stabilization_vm: StabilizationControlViewModel,
        envelope_vm: EnvelopeControlViewModel,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._spec_handle = spec_handle
        self._svc = phase_control_svc
        self._stabilization_vm = stabilization_vm
        self._envelope_vm = envelope_vm
        self._last_spectrum: tuple[np.ndarray, np.ndarray] | None = None
        spec_handle.register_consumer(self.CONSUMER_ID)
        self._sub(SpectrumAvailable, self._on_spectrum)

    @property
    def svc(self) -> PhaseControlService:
        return self._svc

    @property
    def stabilization_vm(self) -> StabilizationControlViewModel:
        return self._stabilization_vm

    @property
    def envelope_vm(self) -> EnvelopeControlViewModel:
        return self._envelope_vm

    def set_mode(self, mode: ControlMode) -> None:
        self._svc.set_mode(mode)

    def save_spectrum_csv(self, path: str) -> None:
        """Write the most recently received raw spectrum to a CSV file."""
        if self._last_spectrum is None:
            self._msg("No spectrum to save yet.", MessageLevel.WARNING)
            return
        wavelengths, intensities = self._last_spectrum
        try:
            np.savetxt(
                path,
                np.column_stack((wavelengths, intensities)),
                delimiter=",",
                header="wavelength_nm,intensity",
                comments="",
            )
        except Exception as exc:
            self._msg(f"Failed to save spectrum: {exc}", MessageLevel.ERROR)
            return
        self._msg(f"Spectrum saved to {path}", MessageLevel.INFO)

    def on_close(self) -> None:
        self._spec_handle.unregister_consumer(self.CONSUMER_ID)
        super().on_close()

    @ui_thread
    def _on_spectrum(self, event: SpectrumAvailable) -> None:
        try:
            buf = self._spec_handle.buffer
            wavelengths = buf.wavelengths(event.slot)
            intensities = buf.intensities(event.slot)
            # Copy out of shared memory so the cached spectrum stays valid after
            # the slot is acked and reused by the writer.
            self._last_spectrum = (np.array(wavelengths), np.array(intensities))
            self.spectrum_updated.emit(wavelengths, intensities)
        except Exception as exc:
            self._msg(f"Spectrum read error: {exc}", MessageLevel.WARNING)
        finally:
            self._bus.publish(SpectrumAck(slot=event.slot, item_id=event.item_id, consumer_id=self.CONSUMER_ID))
