from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from base_core.math.enums import AngleUnit
from base_core.math.models import Angle

PHASE_TOLERANCE = Angle(10, AngleUnit.DEG)
CONVERSION_CONST = 1 / 4
CORRECTION_SIGN = -1
# Fraction of the measured error corrected per frame. Corrections are relative, so the
# loop integrates and this alone sets its bandwidth: ~1/LOOP_GAIN frames to pull in.
# Deliberately slow. The phase noise is faster than the ~0.5 s measure-and-move cycle
# and so cannot be tracked; chasing it just injects it into the stage. We correct
# long-term drift and average the noise away. This also keeps the loop overdamped
# despite the dead time, which a gain of 1 would not.
LOOP_GAIN = 0.15


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

    @property
    def target_phase(self) -> Angle:
        return self._target_phase

    @target_phase.setter
    def target_phase(self, value: Angle) -> None:
        self._target_phase = value

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

    @staticmethod
    def _convert_phase_to_hwp(phase: Angle) -> Angle:
        hwp_deg = CORRECTION_SIGN * phase.Deg * CONVERSION_CONST * LOOP_GAIN
        # wrap=False: an increment is not a point on the circle.
        return Angle(hwp_deg, AngleUnit.DEG, wrap=False)
