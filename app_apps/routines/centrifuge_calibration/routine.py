from __future__ import annotations

import numpy as np

from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.routines.routine import Routine
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer

from app_apps.routines.centrifuge_calibration.events import AnalysisResult, CalibrationApplied
from app_apps.routines.centrifuge_calibration.steps.analysis_step import AnalysisStep
from app_apps.routines.centrifuge_calibration.steps.calibration_step import CalibrationStep
from app_apps.routines.centrifuge_calibration.steps.spectrum_collection_step import SpectrumCollectionStep

_N_SPECTRA = 5


class CentrifugeCalibrationRoutine(Routine):
    """
    Calibrates the centrifuge by correlating spectral measurements with a
    rotation-stage correction.

    Lifecycle
    ---------
    start()             → begin collecting spectra (SpectrumCollectionStep)
                          → on ready: run analysis off-thread (AnalysisStep)
                          → on complete: publish AnalysisResult for VM, loop back
    activate_control()  → user triggers CalibrationStep with the last result
    stop()              → abort everything, safe to call at any point
    """

    def __init__(
        self,
        bus: EventBus,
        io: TaskRunner,
        spectrum_buffer: SharedSpectrumBuffer,
    ) -> None:
        super().__init__(bus, io)
        self._last_result: AnalysisResult | None = None

        self._collection_step = SpectrumCollectionStep(
            bus, io, spectrum_buffer,
            n_spectra=_N_SPECTRA,
            on_ready=self._on_spectra_ready,
        )
        self._analysis_step = AnalysisStep(
            bus, io,
            on_complete=self._on_analysis_complete,
            on_error=self._on_error,
        )
        self._calibration_step = CalibrationStep(
            bus, io,
            on_complete=self._on_calibration_complete,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._last_result = None
        self._collection_step.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._collection_step.stop()
        self._analysis_step.stop()
        self._calibration_step.stop()

    def activate_control(self) -> None:
        """Apply a calibration correction based on the last analysis result.

        Called from the VM when the user confirms they want to send the
        correction to hardware.  No-op if the routine isn't running or no
        analysis result is available yet.
        """
        if not self._running or self._last_result is None:
            return
        self._calibration_step.start(self._last_result)

    # ------------------------------------------------------------------
    # Step transitions
    # ------------------------------------------------------------------

    def _on_spectra_ready(self, spectra: list[np.ndarray]) -> None:
        if not self._running:
            return
        self._analysis_step.start(spectra)

    def _on_analysis_complete(self, result: AnalysisResult) -> None:
        if not self._running:
            return
        self._last_result = result
        self._bus.publish(result)           # VM subscribes to display fit parameters
        self._collection_step.start()       # loop: collect next batch

    def _on_calibration_complete(self, applied: CalibrationApplied) -> None:
        if not self._running:
            return
        # After applying a correction restart collection to verify the result
        self._collection_step.start()

    def _on_error(self, exc: BaseException) -> None:
        # TODO: publish an AppMessage or a dedicated error event for the VM
        self.stop()
