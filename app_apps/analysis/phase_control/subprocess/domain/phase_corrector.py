from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from base_core.math.enums import AngleUnit
from base_core.math.models import Angle

PHASE_TOLERANCE = Angle(10, AngleUnit.DEG)
CONVERSION_CONST = 1 / 4
CORRECTION_SIGN = -1


@dataclass(frozen=True)
class CorrectionResult:
    angle: Angle  # signed HWP rotation angle
    sign: int     # +1 or -1, direction of rotation


@dataclass
class PhaseCorrector:
    """
    Convert a measured phase offset into a physical half-wave-plate
    rotation angle, with wrapping and tolerance logic.
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

        phase_error = self._wrap_phase_pi(Angle(phase - self._target_phase))

        if np.abs(phase_error) <= PHASE_TOLERANCE:
            return None

        self._correction_angle = self._convert_phase_to_hwp(phase_error)
        sign = 1 if float(self._correction_angle) >= 0 else -1
        return CorrectionResult(angle=self._correction_angle, sign=sign)

    @staticmethod
    def _wrap_phase_pi(phase: Angle) -> Angle:
        step = math.pi
        k = round(phase / step)
        return Angle(phase - k * step)

    @staticmethod
    def _convert_phase_to_hwp(phase: Angle) -> Angle:
        hwp_deg = CORRECTION_SIGN * phase.Deg * CONVERSION_CONST
        return Angle(hwp_deg, AngleUnit.DEG)
