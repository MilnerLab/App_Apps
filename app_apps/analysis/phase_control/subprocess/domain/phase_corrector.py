from __future__ import annotations

from dataclasses import dataclass
import logging
import math

import numpy as np

from base_core.math.enums import AngleUnit
from base_core.math.models import Angle

log = logging.getLogger(__name__)

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
            log.info("PhaseCorrector: phase==0 exactly -> no correction (treated as 'no fit yet')")
            return None

        phase_error = self._wrap_phase_pi(Angle(phase - self._target_phase))

        log.info(
            "PhaseCorrector: phase=%.3f deg, target=%.3f deg, wrapped_error=%.3f deg, tolerance=%.3f deg",
            phase.Deg, self._target_phase.Deg, phase_error.Deg, PHASE_TOLERANCE.Deg,
        )

        if np.abs(phase_error) <= PHASE_TOLERANCE:
            log.info(
                "PhaseCorrector: |error|=%.3f deg <= tolerance %.3f deg -> WITHIN TOLERANCE, no correction",
                abs(phase_error.Deg), PHASE_TOLERANCE.Deg,
            )
            return None

        self._correction_angle = self._convert_phase_to_hwp(phase_error)
        sign = 1 if float(self._correction_angle) >= 0 else -1
        log.info(
            "PhaseCorrector: error %.3f deg exceeds tolerance -> HWP correction=%.4f deg (sign=%+d). "
            "NOTE: this is a RELATIVE correction; RgvHandle currently applies it via RotateRGVTo "
            "(absolute move_to), which may be the reason the plate does not keep tracking.",
            phase_error.Deg, self._correction_angle.Deg, sign,
        )
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
