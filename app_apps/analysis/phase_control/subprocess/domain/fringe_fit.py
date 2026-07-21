"""Thin adapter between the app and ``fringe_core`` — the fringe analysis.

**There is no math in this file, and none may be added.** All analysis lives in
``fringe_core.py`` (the single source of truth); this module only translates between the
app's frozen dataclasses and that module's ``analyze()``. If you need to change the analysis,
change ``fringe_core.py`` — never re-implement any of it here. (Why a single copy: see
docs/phase_stabilization_fit.md.)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from app_apps.analysis.phase_control.subprocess.domain import fringe_core as fc

log = logging.getLogger(__name__)

# Re-exported so callers that already import these from here keep working.
gauss = fc.gauss
phase_poly = fc.phase_poly
signal_model = fc.signal_model
baseline_anchor = fc.baseline_anchor
ReferencePolicy = fc.ReferencePolicy
ClipCache = fc.ClipCache


@dataclass(frozen=True)
class FitTunables:
    """User-editable inputs. Everything else is a calibrated constant in ``fringe_core``.

    Defaults are IMPORTED from ``fringe_core``, never restated here: a stale constant copied
    into this file is a different analysis from the same math. To expose another knob, alias
    the module constant; never retype its value.
    """

    trunc_threshold: float = fc.TRUNC_THRESHOLD
                                    # keep where the envelope gap >= min + THIS*(max-min).
                                    # See fringe_core.TRUNC_THRESHOLD for the live calibration
                                    # and the record of why it last moved.
    trust_nsig: float = fc.TRUST_NSIG
                                    # require THIS * sigma to fit inside the accuracy spec
                                    # before the phase is reported. The accuracy/yield trade;
                                    # loosen toward 2.0 to commit more frames. See
                                    # fringe_core.TRUST_NSIG (and docs) for the calibration.


@dataclass(frozen=True)
class FringeFitResult:
    """Outcome of one trace fit.

    ``accepted`` is a solver-success flag only — the caller applies its own quality gate
    (``StabilizationConfig.accepts``) on ``rms_frac`` / ``inlier_pct`` / ``trust_ok``.
    """

    accepted: bool
    pU: tuple[float, float, float, float]     # upper envelope Gaussian (a, mu, sigma, off)
    pLn: tuple[float, float, float, float]    # gap (U-L) Gaussian (a, mu, sigma, off)
    l0: float                                 # phase basis origin (nm) = core centroid
    csig: tuple[float, float, float, float]   # cubic phase coeffs (c0..c3) in u = lambda-l0
    phase_ref: float                          # phase_poly(csig, lambda_ref - l0) [rad]
    rms_sig: float                            # raw-signal fit RMS (counts)
    rms_frac: float                           # rms_sig / median(half-amp): scale-free, so the
                                              # accept gate works on bright and dim alike
    inlier_pct: float                         # fraction of core samples within 3*MAD (%)
    has_null: bool                            # frequency null lies inside the core

    status: str = "ok"                        # "ok" | "underdetermined" | "dead_window" |
                                              # "too_few" | "nonfinite" | "error"
    trust_ok: bool = True                     # the data can support the PHASE at ref_wl.
                                              # c0 only -- the one quantity the loop acts on.
    shape_ok: bool = True                     # ...and the data can support the CARRIER and
                                              # CHIRP (c1..c3). Separate because only consumers
                                              # that evaluate the fit AWAY from ref_wl need it
                                              # (the chart overlay and the RF readout). Do NOT
                                              # fold this into trust_ok or accepts().
    ref_wl: float = float("nan")              # WHERE the phase is trustworthy. READ THIS --
                                              # never assume 802: a clip near the core moves
                                              # it to the core centroid.
    ref_fallback: bool = False                # True => ref_wl moved off the spectral centre
    ref_offset_nm: float = 0.0                # |ref_primary - l0|: how far the fitted core
                                              # sits from the reference. The ACCURACY gate's input.
    ref_offset_frac: float = 0.0              # ...as a fraction of the core half-span.
    ref_offset_ok: bool = True                # False => the core drifted off the reference, so
                                              # the phase there is biased by the crop. Enforced
                                              # by StabilizationConfig.accepts, not the fit; see
                                              # fringe_core.REF_MAX_OFFSET_FRAC.
    ref_offset_msg: str = ""                  # ...spelled out, for whoever drops the frame
    csig_sigma: tuple[float, float, float, float] = (0.0,) * 4   # 1-sigma on c0..c3 at ref_wl
    trunc_side: str = "none"                  # clipped arm: none/left/right/both/all/unknown
    trunc_hits_core: bool = False             # the clip landed where the phase wanted to be
                                              # fit (measured on the NOMINAL core, before the
                                              # cut and before the dead-end trim)
    trunc_hits_fit: bool = False              # ...and dead samples are actually IN the fitted
                                              # set. The stronger statement; see fringe_core.
    cut_left: float | None = None             # WHERE the knife edge was found (nm). None = no
    cut_right: float | None = None            # edge on that side. Samples outside [cut_left,
                                              # cut_right] were EXCLUDED from the fit, so this
                                              # is the boundary of what the answer rests on --
                                              # which is why the chart draws it.
    msg: str = ""

    def phase_at(self, lambda_ref_nm: float) -> float:
        """Cubic phase at a reference wavelength (radians, unwrapped).

        NB ``ref_wl`` is where the fit says the phase is *supportable*. Evaluating here at
        some other wavelength is allowed and sometimes right, but it is not covered by
        ``trust_ok``.
        """
        return float(fc.phase_poly(np.asarray(self.csig, float), lambda_ref_nm - self.l0))


def rejected(status: str = "error", msg: str = "") -> FringeFitResult:
    """A fit that failed to converge / had too little signal. NaN-filled."""
    nan4 = (float("nan"),) * 4
    return FringeFitResult(
        accepted=False, pU=nan4, pLn=nan4, l0=float("nan"), csig=nan4,
        phase_ref=float("nan"), rms_sig=float("inf"), rms_frac=float("inf"),
        inlier_pct=0.0, has_null=False, status=status, trust_ok=False, shape_ok=False,
        ref_wl=float("nan"), ref_fallback=False, msg=msg,
    )


def analyze_trace(
    wl: np.ndarray,
    intensity: np.ndarray,
    t: FitTunables,
    anchor: tuple[float, float] | None = None,
    ref_policy: fc.ReferencePolicy | None = None,
    lambda_ref_nm: float | None = None,
    clip_cache: fc.ClipCache | None = None,
    env_center: float | None = None,
    manual_cut_left: float | None = None,
) -> FringeFitResult:
    """Fit one already-windowed trace. Always a cold, independent fit.

    ``lambda_ref_nm`` is the operator's configured reference — the wavelength the phase is
    WANTED at, and the lock point of the stabilization loop. It is honoured unless the data
    cannot support the phase there (a clip near the core), in which case ``ref_wl`` falls
    back to the core centroid and ``ref_fallback`` says so. Pass None only where there is no
    operator preference; the fit then uses the fitted intensity centroid, and the reported
    reference will wander by a fraction of a nm frame to frame.

    ``anchor`` is the ``(U_base, D)`` continuum measurement from
    ``fringe_core.baseline_anchor()`` on the **FULL frame**, taken BEFORE this window was
    cut — the analysis window contains no continuum, so the envelope offset has nothing to
    pin it. ``PhaseTracker`` measures it before windowing and passes it down. Omitting it is
    safe on dim traces and wrong on bright ones.

    ``ref_policy`` is a ``ReferencePolicy`` carried ACROSS frames by the caller, so the
    reported reference cannot chatter between two wavelengths. Omit it and the reference
    falls back immediately.

    ``clip_cache`` is a ``ClipCache`` carried across frames by the caller: it remembers the
    knife edge in WAVELENGTH so a clipped frame need not re-run the ~645 ms recovery scan to
    rediscover a knife that has not moved. It is consulted only on a frame whose uncut fit
    already failed ``_explains``, so a stale edge cannot steer a clean frame; see
    ``fringe_core._analyze_cached``. This cache MUST be passed for stable clip detection.
    """
    try:
        R = fc.analyze(
            np.asarray(wl, dtype=float), np.asarray(intensity, dtype=float),
            anchor=anchor, ref_policy=ref_policy,
            trust_nsig=t.trust_nsig, trunc_threshold=t.trunc_threshold,
            ref_primary=lambda_ref_nm, clip_cache=clip_cache,
            env_center=env_center, manual_cut_left=manual_cut_left,
        )
    except Exception as e:  # fringe_core already guards its own internals; belt and braces
        log.exception("analyze_trace failed: %s", type(e).__name__)
        return rejected("error", f"{type(e).__name__}: {e}")

    status = R.get("status", "error")
    if R.get("csig") is None:
        # Degenerate trace (dead window / too few points / non-finite). Not an error.
        return rejected(status, R.get("msg", ""))

    half = np.asarray(R["half"], float)
    med_half = float(np.median(half))
    rms_sig = float(R["rms_sig"])
    rms_frac = rms_sig / (med_half + 1e-9)

    resid = np.asarray(R["resid_sig"], float)
    mad = 1.4826 * float(np.median(np.abs(resid))) + 1e-9
    inlier_pct = 100.0 * float((np.abs(resid) < 3.0 * mad).mean())

    trunc = R.get("trunc") or {}
    applied_lo, applied_hi = fc.applied_cuts(trunc)
    ref_wl = float(R["ref_wl"])

    return FringeFitResult(
        accepted=True,
        pU=tuple(float(v) for v in R["pU"]),      # type: ignore[arg-type]
        pLn=tuple(float(v) for v in R["pLn"]),    # type: ignore[arg-type]
        l0=float(R["l0"]),
        csig=tuple(float(v) for v in R["csig"]),  # type: ignore[arg-type]
        phase_ref=float("nan"),                   # filled by the caller via phase_at()
        rms_sig=rms_sig,
        rms_frac=rms_frac,
        inlier_pct=inlier_pct,
        has_null=bool(R["has_null"]),
        status=status,
        trust_ok=bool(R["trust_ok"]),
        shape_ok=bool(R.get("shape_ok", False)),
        ref_wl=ref_wl,
        ref_fallback=bool(R["ref_fallback"]),
        ref_offset_nm=float(R.get("ref_offset_nm", 0.0)),
        ref_offset_frac=float(R.get("ref_offset_frac", 0.0)),
        ref_offset_ok=bool(R.get("ref_offset_ok", True)),
        ref_offset_msg=str(R.get("ref_offset_msg", "")),
        csig_sigma=tuple(float(v) for v in R["csig_sigma"]),  # type: ignore[arg-type]
        trunc_side=str(trunc.get("side", "unknown")),
        trunc_hits_core=bool(trunc.get("hits_core", False)),
        trunc_hits_fit=bool(trunc.get("hits_fit", False)),
        # `fc.applied_cuts`, NOT the raw keys: the detector reports a candidate edge on both
        # sides whenever it finds a dead run, but the fit only honours the side it claims.
        # Reading the raw keys draws a knife edge at 810.98 nm on live_desktop_spectrum,
        # which is a CLEAN trace -- caught by test_knife_edge_reaches_the_ui.
        cut_left=_opt_nm(applied_lo),
        cut_right=_opt_nm(applied_hi),
        msg=str(R.get("msg", "")),
    )


def _opt_nm(v) -> float | None:
    """A cut edge as a plain float, or None when there is no edge on that side.

    NaN is deliberately mapped to None rather than passed through: this value crosses the
    IPC boundary into the persisted config, and a NaN there is both un-plottable and, in
    strict JSON, unrepresentable.
    """
    if v is None:
        return None
    f = float(v)
    return None if f != f else f


def display_curve(r: FringeFitResult, wl: np.ndarray):
    """(mid, half, phase) sampled on ``wl`` for the chart overlay, from a committed fit."""
    x = np.asarray(wl, dtype=float)
    Ud = fc.gauss(x, *r.pU)
    Ld = Ud - fc.gauss(x, *r.pLn)
    mid = 0.5 * (Ud + Ld)
    half = 0.5 * (Ud - Ld)
    phase = fc.phase_poly(np.asarray(r.csig, float), x - r.l0)
    return mid, half, phase
