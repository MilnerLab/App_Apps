from __future__ import annotations

from typing import ClassVar

import numpy as np
from PySide6.QtCore import Signal

from app_apps.analysis.phase_control.events import PhaseTrackingStateChanged, StabilizationRotatorChanged
from app_apps.analysis.phase_control.subprocess.domain.mode import ControlMode
from app_apps.io.control_readout.rotator import HwpRotator
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
    rotator_changed = Signal(object)            # HwpRotator
    rotator_locked_changed = Signal(bool)       # True while the loop is driving the plate

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
        self._sub(StabilizationRotatorChanged, self._on_rotator_changed)
        self._sub(PhaseTrackingStateChanged, self._on_phase_state_changed)

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

    # -- rotator selection ------------------------------------------------------------
    @property
    def rotator(self) -> HwpRotator:
        return self._svc.rotator

    @property
    def rotator_locked(self) -> bool:
        return self._svc.rotator_locked

    def set_rotator(self, rotator: HwpRotator) -> None:
        if self._svc.rotator_locked:
            # The control is disabled in this state; a click that slipped through is snapped
            # back rather than switching the plate under a running loop.
            self._msg("Stop or pause stabilization before switching the rotator.",
                      MessageLevel.WARNING)
            self.rotator_changed.emit(self._svc.rotator)
            return
        self._svc.set_rotator(rotator)
        self._msg(f"Phase stabilization now drives the {rotator.value}.", MessageLevel.INFO)

    @ui_thread
    def _on_rotator_changed(self, event: StabilizationRotatorChanged) -> None:
        self.rotator_changed.emit(event.rotator)

    @ui_thread
    def _on_phase_state_changed(self, _event: PhaseTrackingStateChanged) -> None:
        self.rotator_locked_changed.emit(self._svc.rotator_locked)

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
