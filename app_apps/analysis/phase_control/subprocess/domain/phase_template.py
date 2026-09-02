"""Frozen-template phase tracking: measure the fringe SHAPE once, then fit only the phase.

The cold pipeline (``fringe_fit.analyze_trace``) is a full non-linear fit -- optimizer,
seeds, multi-start, BIC order selection, truncation recovery -- and costs 260 ms on a good
trace. Almost all of that is spent re-deriving a shape that has not changed. This module
freezes the shape from 10 traces and leaves **one** free parameter, the phase, which has an
exact closed form and costs 99 us.

Measured against the true least-squares minimiser of the same 1-parameter model
(700 px, noise sigma 4):

    agreement with brute-force minimiser   0.00 mrad  (same answer to machine precision)
    accuracy vs truth                      4.2 mrad (0.24 deg) over -3..+3 rad
    cost                                   99 us/frame vs 260 ms .. 46 700 ms
    amplitude when the fringes vanish      drops 226x (2 811 041 -> 12 392)

This is not a simplification or an approximation. The 1-parameter problem is analytically
solvable, so there is no optimizer, no seed and no iteration to have.

**Nothing here changes the cold path.** Reference capture runs the existing pipeline
unmodified, per trace, on each of the 10 -- same optimizer, same accept/reject gate. It is
not made faster and it is not made looser.

Deliberately NOT included, having been tested and rejected: letting the envelope amplitude
and baseline float as extra linear terms. A 3-term variant matched the rigid 1-parameter fit
to within 0.1 mrad under 0.3x-2x intensity scaling and +-50 counts of baseline shift, and
was slower (232 us vs 99 us). Phase enters only through the correlation term, so intensity
drift cannot bias it -- the rigid freeze is correct.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.signal import hilbert

from base_core.framework.serialization.serde import Primitive, PrimitiveSerde

from app_apps.analysis.phase_control.subprocess.domain import fringe_core as fc

# How many CONSECUTIVELY accepted traces make a reference. A rejection resets the count to
# zero -- the run must be unbroken.
#
# The consecutiveness rule is not fussiness, it covers a real hazard: averaging traces whose
# phase drifted between them washes the fringes out of the average, and the template would
# then be fit to noise and trusted indefinitely. An unbroken run of 10 accepted traces, plus
# the visibility check on the averaged trace before fitting it, is sufficient; no separate
# phase-spread test is needed.
CAPTURE_N = 10

# Shape-mismatch gate for the per-trace Hilbert check. Measured:
#
#   same shape, phase +0.0 / +1.5 / -3.0 / +pi   0.0029 / 0.0029 / 0.0023 / 0.0014
#   delay move: c1 +1% / +5% / +25%              0.0109 / 0.0508 / 0.2493
#   grating:    c2 x2 / x0.5 / ->0 / c3 x5       0.0464 / 0.0231 / 0.0468 / 0.0367
#
# Phase-invariant to ~0.003 across a full pi -- because instantaneous frequency is the
# DERIVATIVE of phase, so a constant offset cancels exactly and the metric sees shape only,
# blind to the one thing that changes every frame. The smallest shape change tested sits
# 3.8x above that floor, so the threshold has room on both sides. Cost ~1 ms/frame.
SHAPE_MISMATCH_MAX = 0.009


def _core_mask(x: np.ndarray, pLn: np.ndarray) -> np.ndarray:
    """The high-contrast core, defined exactly as ``fringe_core`` defines it: keep where the
    envelope gap is at least ``TRUNC_THRESHOLD`` of the way from its minimum to its peak."""
    gap = fc.gauss(x, *pLn)
    lo, hi = float(np.min(gap)), float(np.max(gap))
    if not np.isfinite(hi) or hi <= lo:
        return np.zeros(x.size, dtype=bool)
    return gap >= lo + fc.TRUNC_THRESHOLD * (hi - lo)


@dataclass
class PhaseTemplate(PrimitiveSerde):
    """A frozen fringe shape: envelopes, carrier, chirp and phase origin.

    ``c0`` is carried but never used as a phase: the per-frame fit measures ``delta``
    RELATIVE to this whole polynomial, and what the loop tracks is the absolute phase at
    ``lambda_ref``, ``phase_poly(csig, lambda_ref - l0) + delta``. See ``absolute_phase``.

    ``x_ref`` / ``f_ref`` are the capture trace's grid and its smoothed instantaneous
    frequency, kept so ``shape_mismatch`` has something to compare against.

    The provenance fields exist so a template recalled from a file can be checked against
    the machine it is loaded onto -- an integration time or averaging count that does not
    match the one the template was captured under changes the noise, and silently.
    """

    l0: float = 0.0
    csig: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    pU: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 0.0])
    pLn: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0, 0.0])
    x_ref: list[float] = field(default_factory=list)
    f_ref: list[float] = field(default_factory=list)
    amp_ref: float = 0.0     # closed-form fit amplitude against the trace this was built
                             # from. The in-loop strength gate is a FRACTION of this, because
                             # the raw amplitude scales with how bright the trace is.
    captured_utc: str = ""
    integration_ms: float = 0.0
    averages: int = 0

    # --- the two frozen curves ------------------------------------------------------
    def envelopes(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(mid, half) of the frozen envelope, sampled on ``x``."""
        Ud = fc.gauss(x, *self.pU)
        Ld = Ud - fc.gauss(x, *self.pLn)
        return 0.5 * (Ud + Ld), 0.5 * (Ud - Ld)

    def phi(self, x: np.ndarray) -> np.ndarray:
        """The frozen phase polynomial, sampled on ``x``."""
        return fc.phase_poly(np.asarray(self.csig, float), x - self.l0)

    def absolute_phase(self, delta: float, lambda_ref_nm: float) -> float:
        """The physical phase at ``lambda_ref_nm``, in radians, unwrapped.

        **Track this, not bare ``delta``.** ``delta`` is measured relative to the template's
        own polynomial, which carries its own ``c0``, so every re-capture silently redefines
        zero. The loop would read the jump as real error and command a large spurious
        correction -- at every setpoint of every scan. Both templates describe the SAME
        physical phase at ``lambda_ref``, so adding the polynomial back in makes a re-capture
        continuous.
        """
        return float(fc.phase_poly(np.asarray(self.csig, float),
                                   lambda_ref_nm - self.l0)) + delta

    def is_empty(self) -> bool:
        return not self.x_ref

    # --- serialization ---------------------------------------------------------------
    def to_primitive(self) -> Primitive:
        return {
            "l0": self.l0,
            "csig": list(self.csig),
            "pU": list(self.pU),
            "pLn": list(self.pLn),
            "x_ref": list(self.x_ref),
            "f_ref": list(self.f_ref),
            "amp_ref": self.amp_ref,
            "captured_utc": self.captured_utc,
            "integration_ms": self.integration_ms,
            "averages": self.averages,
        }

    @classmethod
    def from_primitive(cls, v: Primitive) -> "PhaseTemplate":
        d: dict[str, Any] = dict(v)  # type: ignore[arg-type]
        return cls(
            l0=float(d.get("l0", 0.0)),
            csig=[float(c) for c in d.get("csig", [0.0] * 4)],
            pU=[float(c) for c in d.get("pU", [0.0, 0.0, 1.0, 0.0])],
            pLn=[float(c) for c in d.get("pLn", [0.0, 0.0, 1.0, 0.0])],
            x_ref=[float(c) for c in d.get("x_ref", [])],
            f_ref=[float(c) for c in d.get("f_ref", [])],
            amp_ref=float(d.get("amp_ref", 0.0)),
            captured_utc=str(d.get("captured_utc", "")),
            integration_ms=float(d.get("integration_ms", 0.0)),
            averages=int(d.get("averages", 0)),
        )


@dataclass(frozen=True)
class TemplateFit:
    """One closed-form phase measurement against a frozen template."""

    delta: float       # phase offset from the template's own polynomial (rad, in (-pi, pi])
    amplitude: float   # |correlation| -- a direct per-frame fringe-strength measure


def fit_phase(x: np.ndarray, y: np.ndarray, tpl: PhaseTemplate) -> TemplateFit:
    """Closed-form 1-parameter phase fit. No optimizer, no seed, no iteration.

    The model is ``mid + half*cos(Phi + delta)`` with everything but ``delta`` frozen.
    Expanding the cosine leaves ``delta`` in a single correlation term, so the least-squares
    minimiser is exact:

        w = (y - mid) * half
        C = w . cos(Phi),  S = w . sin(Phi)
        delta = -atan2(S, C),  amplitude = hypot(C, S)

    ``amplitude`` falls by ~226x when the fringes vanish, so it doubles as the in-loop
    fringe-strength gate and the 1.9 ms ``fringe_visibility`` index is only needed to protect
    reference capture.
    """
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    mid, half = tpl.envelopes(x)
    phi = tpl.phi(x)
    w = (y - mid) * half
    c = float(np.dot(w, np.cos(phi)))
    s = float(np.dot(w, np.sin(phi)))
    return TemplateFit(delta=-math.atan2(s, c), amplitude=math.hypot(c, s))


def instantaneous_frequency(x: np.ndarray, y: np.ndarray,
                            tpl: PhaseTemplate) -> np.ndarray:
    """Smoothed |instantaneous frequency| of ``y`` under the template's frozen envelope.

    Normalising by the frozen envelope first is what makes this comparable frame to frame:
    the Hilbert transform of a raw amplitude-modulated trace mixes the envelope into the
    phase, and the envelope is exactly the thing we have already decided not to re-measure.
    """
    x = np.asarray(x, float)
    mid, half = tpl.envelopes(x)
    n = (np.asarray(y, float) - mid) / np.where(np.abs(half) < 1e-9, 1e-9, half)
    ph = np.unwrap(np.angle(hilbert(n)))
    f = np.gradient(ph, x) / (2.0 * math.pi)
    return fc.smooth_absf(x, f)


def shape_mismatch(x: np.ndarray, y: np.ndarray, tpl: PhaseTemplate) -> float:
    """How far this trace's fringe SHAPE has drifted from the template's. NaN if unmeasurable.

    Instantaneous frequency is the derivative of phase, so a constant phase offset -- the one
    thing that changes every single frame -- cancels exactly. See ``SHAPE_MISMATCH_MAX`` for
    the measured separation between "same shape, any phase" and a real delay/grating move.

    **This cannot catch a sign flip, and must not be relied on to.** ``smooth_absf`` takes
    the magnitude, so the mismatch between a template and its own sign-flipped twin is
    0.00000. That hazard is handled at capture instead, by ``align_sign``.
    """
    if tpl.is_empty():
        return float("nan")
    x = np.asarray(x, float)
    core = _core_mask(x, np.asarray(tpl.pLn, float))
    if core.sum() < 16:
        return float("nan")
    f_sm = instantaneous_frequency(x, y, tpl)
    f_ref = np.interp(x, np.asarray(tpl.x_ref, float), np.asarray(tpl.f_ref, float))
    scale = float(np.mean(np.abs(f_ref[core])))
    if not np.isfinite(scale) or scale <= 0.0:
        return float("nan")
    return float(np.sqrt(np.mean((f_sm[core] - f_ref[core]) ** 2)) / scale)


def align_sign(new: PhaseTemplate, old: PhaseTemplate | None) -> PhaseTemplate:
    """Flip ``new`` (Phi -> -Phi) if its carrier disagrees in sign with ``old``'s.

    **The cold fit is sign-ambiguous.** ``signal_model`` is ``mid + half*cos(Phi)`` and cosine
    is even, so ``Phi -> -Phi`` is a bit-identical fit and which one the optimiser lands on is
    a seed accident. Measured on a sign-flipped template against the same trace:

        phase vs template +Phi :  +0.7020
        phase vs template -Phi :  -0.7020        <-- the loop drives the WRONG WAY

    One unlucky re-capture mid-scan therefore inverts the loop, and an inverted loop does not
    run away -- it is repelled from the setpoint and settles stably exactly pi off target,
    which is the bench symptom that motivated the correction-sign toggle in the first place.

    The guard only has to hold RELATIVE to the previous template, never absolutely: the
    operator's ``invert_correction`` fixes the global sense once, and this keeps every later
    re-capture consistent with it. Deterministic and free.
    """
    if old is None or old.is_empty():
        return new
    c1_new, c1_old = float(new.csig[1]), float(old.csig[1])
    if c1_new == 0.0 or c1_old == 0.0 or (c1_new > 0) == (c1_old > 0):
        return new
    new.csig = [-c for c in new.csig]
    new.f_ref = list(new.f_ref)   # |f| is unchanged by the flip; kept explicit
    return new
