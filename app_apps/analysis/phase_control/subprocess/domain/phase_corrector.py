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
#
# This is only the DEFAULT -- the operator tunes it live from the config panel
# (StabilizationConfig.loop_gain), because the right value depends on the dead time,
# which depends on the spectrometer's integration/averaging settings. Raise it toward 1
# and the dead time will make the loop ring; that is the failure this default avoids.
LOOP_GAIN = 0.05
# Bounds for the operator's live edit. 0 would silently kill the loop; negative would be
# positive feedback (the correction drives the phase further off, every frame). >1 is
# over-correction on its face: more than the whole measured error, per frame, into a loop
# that already has ~0.5 s of dead time.
GAIN_MIN, GAIN_MAX = 0.01, 1.0
# Hard ceiling on a SINGLE correction, in degrees of plate.
#
# Note what this is and is not. A single step is already bounded: `phase_error` is wrapped
# to (-pi, pi], so the largest step the arithmetic below can produce is
# 180/4 * gain = 45*gain degrees -- 2.25 deg at the default gain, and this cap never binds
# there. It binds only when the operator has wound `loop_gain` toward GAIN_MAX, where a
# single frame could otherwise ask for 45 deg, i.e. half a wave of phase in one move on a
# loop that already has ~0.5 s of dead time.
#
# It is therefore NOT the fix for a plate that winds through whole turns: that failure is
# an ACCUMULATION of many small, correctly-sized steps that all point the same way, and no
# per-step cap can see it. The accumulation limit lives where the absolute position is
# known -- `RgvHandle.RGV_MAX_DEG`. This is only the guard against one frame moving the
# plate a long way on a bad number.
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
        # Clamped, not validated: this arrives from a live UI edit mid-run, and a stray
        # 0 (loop silently dead) or a negative (positive feedback -- runs the phase away
        # and keeps going) must not reach the stage. The UI enforces the same bounds; this
        # is the one that matters, because it is the one the hardware is behind.
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
        # Clamped, not rejected: a step this large still points the right way, and
        # refusing it would stall the loop exactly when it has the most to correct.
        # See MAX_STEP_DEG -- this does not bind at the default gain.
        hwp_deg = min(max(hwp_deg, -MAX_STEP_DEG), MAX_STEP_DEG)
        # wrap=False: an increment is not a point on the circle.
        return Angle(hwp_deg, AngleUnit.DEG, wrap=False)
