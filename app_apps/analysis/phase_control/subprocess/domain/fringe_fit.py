"""Thin adapter between the app and ``fringe_core`` — the fringe analysis.

**There is no math in this file, and none may be added.** All analysis lives in
``fringe_core.py`` (the single source of truth); this module only translates between the
app's frozen dataclasses and that module's ``analyze()``.

That separation is the whole point. Until 2026-07-16 this file carried a hand-maintained
second copy of the math, and every bug found that day was drift between the two copies:

  * ``fit_ftol=1e-4`` was passed as L-BFGS-B's *relative* ``ftol`` (its default is 2.22e-9),
    so the envelope fit quit after 19 iterations and returned offset **255** against a truth
    of **155** on the real bright trace — squeezing sigma ~12% narrow and inflating
    ``rms_frac``, i.e. feeding the accept gate that was rejecting live frames;
  * ``cubic_init = [C, 0.0, A, 0.0]`` forced the carrier ``c1 = 0``, and the soft-L1 fit
    never climbed out: measured, this copy reported c1 of -0.15/-4.03/-3.02/-0.39 on four
    real traces where ``fringe_core`` reads 6.65/23.81/8.20/7.68. The carrier — the quantity
    phase stabilization locks to — was wrong on **every trace**;
  * the baseline anchor, the truncated-arm detector, BIC phase-order selection and the trust
    gate simply never arrived.

None of that was a hard bug to write; it was inevitable given two copies. ``analyze_trace``
now delegates to ``fringe_core`` and nothing else. If you need to change the analysis, change
``fringe_core.py`` — never re-implement any of it here.
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

    **The defaults are IMPORTED from ``fringe_core``, never written out here.** Copying a
    calibrated number into this file is the same drift bug as copying the math: a stale
    ``trunc_threshold = 0.40`` restated here while ``fringe_core`` had recalibrated
    ``TRUNC_THRESHOLD`` to 0.30 threw the carrier off by up to 24.6 rad/nm on a real trace —
    the same math fed a different constant is a different analysis. If you need another knob,
    alias the module constant; never retype its value.

    The knobs the old folded-chirp fitter needed (``phase_loss_scale``, ``init_smooth_div``,
    ``inlier_nsigma``, ``fit_ftol``) are GONE: that pipeline is gone. ``fit_ftol`` in
    particular must not come back — it was the L-BFGS-B units bug, and the envelope fit is
    Nelder-Mead now precisely because a kinked loss makes it unconditionally safe.
    """

    trunc_threshold: float = fc.TRUNC_THRESHOLD
                                    # keep where the envelope gap >= min + THIS*(max-min).
                                    # See fringe_core.TRUNC_THRESHOLD for the live calibration
                                    # and the record of why it last moved.
    trust_nsig: float = fc.TRUST_NSIG
                                    # require THIS * sigma to fit inside the accuracy spec
                                    # before the phase is reported at all. This is the
                                    # accuracy/yield trade, measured over the full operating
                                    # space (2470 fits, two seeds) as accuracy of reported
                                    # fits / fraction of good fits thrown away:
                                    #   2.0 -> 97.97% /  0.8%     4.0 -> 98.99% /  9.0%
                                    #   3.0 -> 98.54% /  3.7%     5.0 -> 99.31% / 15.0%
                                    #   3.25-> 98.69% /  4.9%    16.0 -> 99.86% / 69.5%
                                    # 3.0 is the spec point (>=98% accurate, <=5% dropped)
                                    # with margin on both. Loosen toward 2.0 while aligning
                                    # if you want every frame to commit; do NOT push past
                                    # ~5 for accuracy -- it saturates while the yield
                                    # collapses. See fringe_core's TRUST_NSIG comment.


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

    # --- v3 additions ------------------------------------------------------------------
    status: str = "ok"                        # "ok" | "underdetermined" | "dead_window" |
                                              # "too_few" | "nonfinite" | "error"
    trust_ok: bool = True                     # the data can support the PHASE at ref_wl.
                                              # c0 only -- the one quantity the loop acts on.
    shape_ok: bool = True                     # ...and the data can support the CARRIER and
                                              # CHIRP (c1..c3). Separate because only things
                                              # that evaluate the fit AWAY from ref_wl need
                                              # it: the chart overlay and the GHz frequency
                                              # readout, which extrapolate across 793-811 nm
                                              # where a wrong c2 enters as d^2. Measured on
                                              # 1240 test traces: 11 of the 13 fits the
                                              # four-coefficient grader calls "wrong" have a
                                              # CORRECT phase and fail only on shape, and
                                              # shape_ok flags 8 of those 11. Gating the loop
                                              # on shape threw those frames away for an error
                                              # it does not care about. Do NOT fold this back
                                              # into trust_ok or accepts().
    ref_wl: float = float("nan")              # WHERE the phase is trustworthy. READ THIS --
                                              # never assume 802: a clip near the core moves
                                              # it to the core centroid.
    ref_fallback: bool = False                # True => ref_wl moved off the spectral centre
    ref_offset_nm: float = 0.0                # |ref_primary - l0|: how far the fitted core
                                              # sits from the reference the phase is wanted
                                              # at. The ACCURACY gate's input.
    ref_offset_frac: float = 0.0              # ...as a fraction of the core half-span.
    ref_offset_ok: bool = True                # False => the core drifted off the reference,
                                              # so the phase there would be biased by the
                                              # crop rather than measured. Enforced by
                                              # StabilizationConfig.accepts, NOT by the fit:
                                              # see fringe_core.REF_MAX_OFFSET_FRAC.
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
    cut — the analysis window is +-3.1 sigma around the bump and contains no continuum at
    all, so the envelope offset has nothing to pin it and the tau-quantile loss floats it
    upward. ``PhaseTracker`` measures it before windowing and passes it down. Omitting it is
    safe on dim traces and wrong on bright ones (offset 164.9 vs a truth of 155.0).

    ``ref_policy`` is a ``ReferencePolicy`` carried ACROSS frames by the caller, so the
    reported reference cannot chatter between two wavelengths. Omit it and the reference
    falls back immediately.

    ``clip_cache`` is a ``ClipCache``, likewise carried across frames by the caller. It
    remembers the knife edge in WAVELENGTH so a clipped frame does not have to re-run the
    ~645 ms recovery scan to rediscover a knife that has not moved. Omitting it was the
    live bug found 2026-07-20: the cache was fully implemented here and never passed, so
    every frame scanned cold, the scan is under a wall-clock budget, and the edge was
    therefore found on some frames and missed on others within the same second -- which
    reads as an intermittent detector and is really an unwired cache. The cache can only
    ever be consulted on a frame whose UNCUT fit already failed ``_explains``, so a stale
    edge cannot silently steer a clean frame; see ``fringe_core._analyze_cached``.
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
