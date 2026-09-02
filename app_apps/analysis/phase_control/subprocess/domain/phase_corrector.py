from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from base_core.math.enums import AngleUnit
from base_core.math.models import Angle

# Deadband: below this much phase error the loop does nothing. It is not a nicety -- every
# correction is a physical plate move with its own settling and backlash, so correcting a
# 0.02 rad error costs more disturbance than it removes. 0.1 rad (5.7 deg) is the default and
# it is a CONFIG value (StabilizationConfig.correction_deadband_rad), because how small an
# error is worth moving for depends on the experiment.
DEADBAND_RAD = 0.1
PHASE_TOLERANCE = Angle(DEADBAND_RAD, AngleUnit.RAD)
CONVERSION_CONST = 1 / 4
# Baseline direction: which way the plate must turn to REDUCE a positive phase error.
# It is not a free parameter of the loop -- it is a property of the optics, and it FLIPS
# with the quarter-wave plate's orientation. The same +0.1 rad of error calls for opposite
# rotations depending on how the QWP is set, so the operator gets a toggle
# (StabilizationConfig.invert_correction) rather than this constant being retuned.
#
# Symptom of getting it wrong: the loop does NOT run away. Wrapping to (-pi, pi] means the
# error changes sign at +-pi, so an inverted loop is repelled from the setpoint and settles
# at the OTHER fixed point -- stable, and exactly pi off target. A stable lock half a turn
# from where you asked for it is the signature of this sign, not of a bad gain.
CORRECTION_SIGN = -1
# What this means depends on WHICH loop is running, and the two readings are not the same
# number wearing one name:
#
#   fast (cold) loop   the fraction of the measured error corrected per frame. Corrections
#                      are relative, so the loop integrates and this alone sets its
#                      bandwidth: ~1/LOOP_GAIN frames to pull in.
#   slow (template)    NOT the step size -- that correction runs at gain 1.0 and drives the
#                      averaged error to zero in one move. Here it is the weight in
#                      PhaseAverager's circular EWMA, so it sets the averaging MEMORY:
#                      ~1/LOOP_GAIN frames. 0.05 is "average the last 20 frames".
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

    Errors inside ``deadband`` produce no correction at all -- ``update`` returns None.
    """
    _correction_angle: Angle = Angle(0, AngleUnit.DEG)
    _target_phase: Angle = Angle(0, AngleUnit.DEG)
    _gain: float = LOOP_GAIN
    _invert: bool = False
    _deadband: Angle = PHASE_TOLERANCE

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

    @property
    def deadband(self) -> Angle:
        """Phase error below which no correction is issued. See DEADBAND_RAD."""
        return self._deadband

    @deadband.setter
    def deadband(self, value: float | Angle) -> None:
        # Clamped at zero only: a negative deadband is meaningless, but there is no upper
        # bound to enforce -- a large one is a deliberate "hold unless it is badly off".
        rad = float(value.Rad if isinstance(value, Angle) else value)
        self._deadband = Angle(max(rad, 0.0), AngleUnit.RAD)

    @property
    def invert(self) -> bool:
        return self._invert

    @invert.setter
    def invert(self, value: bool) -> None:
        self._invert = bool(value)

    def update(self, phase: Angle) -> CorrectionResult | None:
        if phase == 0.0:
            return None

        # Angle() wraps to (-pi, pi], so this is already the shortest way round.
        phase_error = Angle(phase - self._target_phase)

        if np.abs(phase_error) <= self._deadband:
            return None

        self._correction_angle = self._convert_phase_to_hwp(phase_error)
        sign = 1 if float(self._correction_angle) >= 0 else -1
        return CorrectionResult(angle=self._correction_angle, sign=sign)

    def _convert_phase_to_hwp(self, phase: Angle) -> Angle:
        sign = -CORRECTION_SIGN if self._invert else CORRECTION_SIGN
        hwp_deg = sign * phase.Deg * CONVERSION_CONST * self._gain
        # wrap=False: an increment is not a point on the circle.
        return Angle(hwp_deg, AngleUnit.DEG, wrap=False)
