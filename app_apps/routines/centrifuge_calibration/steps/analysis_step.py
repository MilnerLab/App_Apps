from __future__ import annotations

from typing import Callable

import numpy as np

from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.routines.step import Step

from app_apps.routines.centrifuge_calibration.events import AnalysisResult


class AnalysisStep(Step):
    """
    Fits collected spectra off-thread and fires on_complete with the result.

    Runs in the Routine's TaskRunner so it doesn't block the Qt main thread.
    The _active flag guards against late on_success callbacks arriving after
    the Routine has been aborted.
    """

    def __init__(
        self,
        bus: EventBus,
        io: TaskRunner,
        *,
        on_complete: Callable[[AnalysisResult], None],
        on_error: Callable[[BaseException], None],
    ) -> None:
        super().__init__(bus, io)
        self._on_complete = on_complete
        self._on_error = on_error
        self._active = False

    def start(self, spectra: list[np.ndarray]) -> None:
        self._active = True
        self._io.run(
            lambda: _fit_spectra(spectra),
            on_success=self._on_success,
            on_error=self._on_error,
            key="centrifuge_calibration.analysis",
            cancel_previous=True,
        )

    def stop(self) -> None:
        # TaskRunner drops the callback via drop_outdated when a new task
        # supersedes; _active guards the case where stop() is called with no
        # following start().
        self._active = False

    def _on_success(self, result: AnalysisResult) -> None:
        if not self._active:
            return
        self._active = False
        self._on_complete(result)


def _fit_spectra(spectra: list[np.ndarray]) -> AnalysisResult:
    """
    TODO: replace with real envelope fitting and phase extraction.

    Placeholder: returns mean peak position and amplitude.
    """
    avg = np.mean(spectra, axis=0)
    peak_idx = int(np.argmax(avg))
    # Map pixel index to approximate wavelength (instrument-specific calibration)
    central_wavelength_nm = 796.0 + peak_idx * (810.0 - 796.0) / len(avg)
    return AnalysisResult(
        central_wavelength_nm=central_wavelength_nm,
        bandwidth_nm=7.5,           # TODO: fit from envelope
        envelope_peak=float(avg[peak_idx]),
        fit_residual=0.0,           # TODO: lmfit residual
    )
