from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from base_core.math.enums import AngleUnit
from base_core.math.models import Angle

PHASE_TOLERANCE = Angle(10, AngleUnit.DEG)   # dead-band: ignore sub-tolerance phase error
CONVERSION_CONST = 1 / 4
CORRECTION_SIGN = -1
# Fraction of the measured error corrected per frame. Corrections are relative, so the loop
# integrates and this alone sets its bandwidth (~1/LOOP_GAIN frames to pull in). Deliberately
# slow/overdamped for the ~0.5 s dead time; the operator tunes it live via loop_gain. See docs.
LOOP_GAIN = 0.05
GAIN_MIN, GAIN_MAX = 0.01, 1.0   # bounds for the live edit (0 kills the loop, <0 is positive feedback)
# Hard ceiling on a SINGLE correction (degrees of plate). Binds only when the gain is wound
# toward GAIN_MAX. It does NOT bound an accumulation of same-sign steps -- that limit lives
# where absolute position is known (RgvHandle.RGV_MAX_DEG).
MAX_STEP_DEG = 5.0


@dataclass(frozen=True)
class CorrectionResult:
    angle: Angle  # signed HWP rotation increment, applied *relative* to where the stage is
    sign: int     # +1 or -1, direction of rotation


@dataclass
class PhaseCorrector:
    """
    Convert a measured phase offset into a physical half-wave-plate
    rotation angle, with wrapping and tolerance logic.

    The result is a relative increment, never an absolute position: the corrector
    never knows where the stage is, only how far off the phase is.
    """
    _correction_angle: Angle = Angle(0, AngleUnit.DEG)
    _target_phase: Angle = Angle(0, AngleUnit.DEG)
    _gain: float = LOOP_GAIN

    @property
    def target_phase(self) -> Angle:
        return self._target_phase

    @target_phase.setter
    def target_phase(self, value: Angle) -> None:
        self._target_phase = value

    @property
    def gain(self) -> float:
        return self._gain

    @gain.setter
    def gain(self, value: float) -> None:
        # Clamped, not validated: arrives from a live UI edit mid-run, and a stray 0 or
        # negative must not reach the stage.
        self._gain = min(max(float(value), GAIN_MIN), GAIN_MAX)

    def update(self, phase: Angle) -> CorrectionResult | None:
        if phase == 0.0:
            return None

        # Angle() wraps to (-pi, pi], so this is already the shortest way round.
        phase_error = Angle(phase - self._target_phase)

        if np.abs(phase_error) <= PHASE_TOLERANCE:
            return None

        self._correction_angle = self._convert_phase_to_hwp(phase_error)
        sign = 1 if float(self._correction_angle) >= 0 else -1
        return CorrectionResult(angle=self._correction_angle, sign=sign)

    def _convert_phase_to_hwp(self, phase: Angle) -> Angle:
        hwp_deg = CORRECTION_SIGN * phase.Deg * CONVERSION_CONST * self._gain
        hwp_deg = min(max(hwp_deg, -MAX_STEP_DEG), MAX_STEP_DEG)   # clamp, don't reject
        # wrap=False: an increment is not a point on the circle.
        return Angle(hwp_deg, AngleUnit.DEG, wrap=False)
