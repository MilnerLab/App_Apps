from __future__ import annotations

import math
from typing import Callable

from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.routines.step import Step
from base_core.math.models import Angle

from app_apps.io.control_readout.events import RotateRequested
from app_apps.routines.centrifuge_calibration.events import AnalysisResult, CalibrationApplied

# Target central wavelength for calibration
_TARGET_WAVELENGTH_NM = 802.38


class CalibrationStep(Step):
    """
    Translates the latest analysis result into a hardware correction.

    Computes a rotation angle from the spectral deviation from target,
    publishes RotateRequested so the control readout service acts on it,
    then fires on_complete.

    Activated explicitly by the user via the VM — not started automatically
    after analysis.
    """

    def __init__(
        self,
        bus: EventBus,
        io: TaskRunner,
        *,
        on_complete: Callable[[CalibrationApplied], None],
    ) -> None:
        super().__init__(bus, io)
        self._on_complete = on_complete
        self._active = False

    def start(self, result: AnalysisResult) -> None:
        self._active = True
        self._io.run(
            lambda: _compute_correction(result),
            on_success=self._on_correction_ready,
            key="centrifuge_calibration.correction",
            cancel_previous=True,
        )

    def stop(self) -> None:
        self._active = False

    def _on_correction_ready(self, correction: _Correction) -> None:
        if not self._active:
            return
        self._active = False
        self._bus.publish(RotateRequested(angle=correction.angle, sign=correction.sign))
        self._bus.publish(CalibrationApplied(correction_deg=correction.deg))
        self._on_complete(CalibrationApplied(correction_deg=correction.deg))


class _Correction:
    def __init__(self, angle: Angle, sign: int) -> None:
        self.angle = angle
        self.sign = sign
        self.deg = math.degrees(float(angle.Rad)) * sign


def _compute_correction(result: AnalysisResult) -> _Correction:
    """
    TODO: replace with real grating / delay-stage calibration model.

    Placeholder: proportional correction from wavelength deviation.
    """
    deviation_nm = result.central_wavelength_nm - _TARGET_WAVELENGTH_NM
    # Rough conversion: 1 nm deviation → 0.5 deg correction (instrument-specific)
    correction_deg = deviation_nm * 0.5
    sign = 1 if correction_deg >= 0 else -1
    angle = Angle(abs(correction_deg))
    return _Correction(angle=angle, sign=sign)
