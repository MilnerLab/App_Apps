from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from base_core.math.enums import AngleUnit
from base_core.math.models import Angle

# Legacy default, restored. The loop holds when the block-averaged error is inside this and
# corrects the WHOLE error when it is outside -- there is no proportional band and no gain.
PHASE_TOLERANCE = Angle(10, AngleUnit.DEG)

# Converts phase error [deg] to half-wave-plate rotation [deg]. A property of the optics:
# rotating the HWP by theta moves the phase by 4*theta.
CONVERSION_CONST = 1 / 4
# Baseline direction: which way the plate must turn to REDUCE a positive phase error. Not a
# free parameter -- it FLIPS with the quarter-wave plate's orientation, so the operator gets
# a toggle (StabilizationConfig.invert_correction) rather than this constant being retuned.
CORRECTION_SIGN = -1

# How many accepted fits go into one correction. The block is NON-OVERLAPPING: it is cleared
# the moment it is used, so every averaged error is built from frames taken entirely after
# the previous move. That is the property the EWMA did not have -- its window straddled the
# correction, mixing pre-move and post-move phases into one number and then correcting on it.
AVG_SPECTRA = 10


class PhaseBatch:
    """A non-overlapping block of ``size`` phases and their circular mean.

    Circular, not arithmetic: phases live on a circle, so averaging 0.05 and 6.23 rad
    arithmetically gives pi (the opposite side) instead of ~0. The legacy loop averaged
    lmfit's raw phase parameter and got away with it because the fit kept the value
    continuous; the current fit reports phase mod 2pi, so the mean has to be done properly.

    ``take`` returns the mean and CLEARS -- there is no way to read the mean without
    consuming the block, which is what keeps a correction from ever being issued twice off
    the same frames.
    """

    def __init__(self, size: int = AVG_SPECTRA) -> None:
        self._size = max(1, int(size))
        self._phases: list[float] = []

    @property
    def size(self) -> int:
        return self._size

    @property
    def count(self) -> int:
        return len(self._phases)

    @property
    def full(self) -> bool:
        return len(self._phases) >= self._size

    def resize(self, size: int) -> None:
        """Change the block length, discarding what is collected so far.

        Discarding is deliberate: a block half-filled at the old length is not a valid
        prefix of a block at the new one, and keeping it would make the first correction
        after an edit average a number of frames the operator never asked for.
        """
        size = max(1, int(size))
        if size == self._size:
            return
        self._size = size
        self._phases.clear()

    def add(self, phase_rad: float) -> None:
        if len(self._phases) < self._size:
            self._phases.append(float(phase_rad))

    def clear(self) -> None:
        self._phases.clear()

    def take(self) -> float | None:
        """The circular mean of the block, consuming it. None if the block is not full."""
        if not self.full:
            return None
        arr = np.asarray(self._phases, dtype=float)
        self._phases.clear()
        z = np.exp(1j * arr).mean()
        if not np.isfinite(z) or abs(z) == 0.0:
            # Every frame cancelled: the mean angle is undefined, not zero. Report nothing
            # rather than hand the stage an arbitrary direction.
            return None
        return float(np.angle(z))

    def mean_now(self) -> float | None:
        """The circular mean of what is collected so far, WITHOUT consuming the block.

        Diagnostic only, and deliberately separate from ``take``: the loop still has exactly
        one way to obtain a correctable mean, and it is the one that clears. This exists so
        the panel can say how far off the block currently sits -- a number the operator reads
        while the block fills, and which the count and coherence together cannot give.
        """
        if not self._phases:
            return None
        z = np.exp(1j * np.asarray(self._phases, dtype=float)).mean()
        if not np.isfinite(z) or abs(z) == 0.0:
            return None
        return float(np.angle(z))

    def coherence(self) -> float:
        """|z| over the frames collected so far: 1 = they all agreed, ~0 = they cancelled.

        Diagnostic only -- nothing gates on it. It is what tells a quiet block apart from
        one that is averaging noise, which the count alone cannot.
        """
        if not self._phases:
            return 0.0
        return float(abs(np.exp(1j * np.asarray(self._phases, dtype=float)).mean()))


@dataclass(frozen=True)
class CorrectionResult:
    angle: Angle  # signed HWP rotation increment, applied *relative* to where the stage is
    sign: int     # +1 or -1, direction of rotation


@dataclass
class PhaseCorrector:
    """Convert a measured phase offset into a physical half-wave-plate rotation.

    Restored to the legacy control law, which is deliberately not a servo:

        err = wrap_pi(phase - target)
        if |err| <= tolerance:  do nothing
        else:                   rotate by -err/4, the WHOLE error, in one move

    There is no gain. The loop's stability comes from correcting on a block-averaged error
    at full step, not from taking a fraction of a noisy per-frame one -- a fractional gain
    small enough to be stable made the pull-in slower than the averaging window, so the
    window spanned the correction and the loop chased its own moves.

    The result is a relative increment, never an absolute position: the corrector never
    knows where the stage is, only how far off the phase is.
    """
    _correction_angle: Angle = Angle(0, AngleUnit.DEG)
    _target_phase: Angle = Angle(0, AngleUnit.DEG)
    _tolerance: Angle = PHASE_TOLERANCE
    _invert: bool = False

    @property
    def target_phase(self) -> Angle:
        return self._target_phase

    @target_phase.setter
    def target_phase(self, value: Angle) -> None:
        self._target_phase = value

    @property
    def tolerance(self) -> Angle:
        return self._tolerance

    @tolerance.setter
    def tolerance(self, value: Angle) -> None:
        self._tolerance = value

    @property
    def invert(self) -> bool:
        return self._invert

    @invert.setter
    def invert(self, value: bool) -> None:
        self._invert = bool(value)

    def wrap_error(self, phase: Angle) -> Angle:
        """The folded error of ``phase`` against the target -- what update() tests.

        Exposed so the panel can quote the SAME number the deadband is applied to. Reporting
        a differently wrapped error would make the loop look like it was ignoring an error it
        never saw.
        """
        return self._wrap_phase_pi(Angle(phase - self._target_phase))

    def update(self, phase: Angle) -> CorrectionResult | None:
        phase_error = self.wrap_error(phase)

        if np.abs(phase_error) <= self._tolerance:
            return None

        self._correction_angle = self._convert_phase_to_hwp(phase_error)
        sign = 1 if float(self._correction_angle) >= 0 else -1
        return CorrectionResult(angle=self._correction_angle, sign=sign)

    @staticmethod
    def _wrap_phase_pi(phase: Angle) -> Angle:
        """Fold to the nearest multiple of pi, i.e. into [-pi/2, +pi/2].

        This is the legacy law, restored verbatim, and it is modulo PI -- not 2*pi. A phase
        exactly pi from target therefore reads as ZERO error and the loop holds there.

        That is not an oversight to be tidied away. The measured quantity is a fringe
        pattern and the HWP conversion is 1/4, so the loop is only ever asked to move the
        plate by at most pi/4 in phase terms; folding at pi keeps every commanded move
        inside that range. Wrapping to 2*pi instead lets a near-pi error command a move
        twice as large as anything the optics were characterised over, which is the regime
        where the previous loop shot away. If the two fixed points need telling apart, that
        is what invert_correction is for -- not this wrap.
        """
        step = math.pi
        k = round(float(phase) / step)
        return Angle(float(phase) - k * step, wrap=False)

    def _convert_phase_to_hwp(self, phase: Angle) -> Angle:
        sign = -CORRECTION_SIGN if self._invert else CORRECTION_SIGN
        hwp_deg = sign * phase.Deg * CONVERSION_CONST
        # wrap=False: an increment is not a point on the circle.
        return Angle(hwp_deg, AngleUnit.DEG, wrap=False)
