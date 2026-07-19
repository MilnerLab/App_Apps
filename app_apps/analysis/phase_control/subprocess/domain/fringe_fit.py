"""Thin adapter between the app and ``fringe_core`` — the verified fringe analysis.

**There is no math in this file, and none may be added.** ``fringe_core.py`` is a VERBATIM
COPY of the standalone ``Data/20260709/spectrometer/fringe_core.py``; this module only
translates between the app's frozen dataclasses and that module's ``analyze()``.

That rule is the whole point. Until 2026-07-16 this file carried a hand-maintained second
copy of the math, and every bug found that day was drift between the two copies:

  * ``fit_ftol=1e-4`` was passed as L-BFGS-B's *relative* ``ftol`` (its default is 2.22e-9),
    so the envelope fit quit after 19 iterations and returned offset **255** against a truth
    of **155** on the real bright trace — squeezing sigma ~12% narrow and inflating
    ``rms_frac``, i.e. feeding the accept gate that was rejecting live frames;
  * ``cubic_init = [C, 0.0, A, 0.0]`` forced the carrier ``c1 = 0``, and the soft-L1 fit
    never climbed out: measured, the port reported c1 of -0.15/-4.03/-3.02/-0.39 on the four
    real traces where the standalone reads 6.65/23.81/8.20/7.68. The carrier — the quantity
    phase stabilization locks to — was wrong on **every trace**;
  * the baseline anchor, the truncated-arm detector, BIC phase-order selection and the trust
    gate simply never arrived.

None of that was a hard bug to write; it was inevitable given two copies. ``analyze_trace``
now delegates, and ``test/fringe_parity_test.py`` asserts this module and the standalone
agree bit-for-bit on the real traces, so the copies cannot drift again in silence.

If you need to change the analysis, change the standalone ``fringe_core.py``, re-run its
harnesses (``verify_phase.py``, ``synth_test.py``, ``synth_truncation.py``), and copy the
file across whole. Do not patch this side.
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


@dataclass(frozen=True)
class FitTunables:
    """User-editable inputs. Everything else is a calibrated constant in ``fringe_core``.

    **The defaults are IMPORTED from ``fringe_core``, never written out here.** Copying a
    calibrated number into this file is the same drift bug as copying the math, and it bit
    us the same way: after the v3 port the two ``fringe_core.py`` files were byte-identical
    and the parity test still failed by up to 24.6 rad/nm of carrier on a real trace, purely
    because this class said ``trunc_threshold = 0.40`` while the standalone had recalibrated
    ``TRUNC_THRESHOLD`` to 0.30. Byte-identical math fed different constants is a different
    analysis. If you need another knob, alias the module constant; never retype its value.

    The knobs the old folded-chirp fitter needed (``phase_loss_scale``, ``init_smooth_div``,
    ``inlier_nsigma``, ``fit_ftol``) are GONE: that pipeline is gone. ``fit_ftol`` in
    particular must not come back — it was the L-BFGS-B units bug, and the envelope fit is
    Nelder-Mead now precisely because a kinked loss makes it unconditionally safe.
    """

    trunc_threshold: float = fc.TRUNC_THRESHOLD
                                    # keep where the envelope gap >= min + THIS*(max-min).
                                    # Harness-swept; see fringe_core.TRUNC_THRESHOLD for the
                                    # live calibration and the record of why it last moved.
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
                                              # 1240 harness traces: 11 of the 13 fits the
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
    csig_sigma: tuple[float, float, float, float] = (0.0,) * 4   # 1-sigma on c0..c3 at ref_wl
    trunc_side: str = "none"                  # clipped arm: none/left/right/both/all/unknown
    trunc_hits_core: bool = False             # the fringe-free band overlaps the fitted core
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
    """
    try:
        R = fc.analyze(
            np.asarray(wl, dtype=float), np.asarray(intensity, dtype=float),
            anchor=anchor, ref_policy=ref_policy,
            trust_nsig=t.trust_nsig, trunc_threshold=t.trunc_threshold,
            ref_primary=lambda_ref_nm,
        )
    except Exception as e:  # fringe_core already guards its own internals; belt and braces
        log.warning("FITDIAG rejected: %s: %s", type(e).__name__, e)
        return rejected("error", f"{type(e).__name__}: {e}")

    status = R.get("status", "error")
    if R.get("csig") is None:
        # Degenerate trace (dead window / too few points / non-finite). Not an error.
        log.warning("FITDIAG rejected [%s]: %s", status, R.get("msg", ""))
        return rejected(status, R.get("msg", ""))

    half = np.asarray(R["half"], float)
    med_half = float(np.median(half))
    rms_sig = float(R["rms_sig"])
    rms_frac = rms_sig / (med_half + 1e-9)

    resid = np.asarray(R["resid_sig"], float)
    mad = 1.4826 * float(np.median(np.abs(resid))) + 1e-9
    inlier_pct = 100.0 * float((np.abs(resid) < 3.0 * mad).mean())

    trunc = R.get("trunc") or {}
    ref_wl = float(R["ref_wl"])

    if log.isEnabledFor(logging.WARNING):
        c = R["csig"]
        log.warning(
            "FITDIAG core=%d span=%.1fnm q=%d null=%s | c=[%.4g,%.4g,%.4g,%.4g] "
            "| ref=%.2fnm%s trust=%s | trunc=%s%s | rms=%.1f rms_frac=%.3f inl=%.0f%% %.0fms",
            len(R["x"]), float(R["x"][-1] - R["x"][0]), R["order"], R["has_null"],
            c[0], c[1], c[2], c[3], ref_wl,
            " (MOVED off centre)" if R["ref_fallback"] else "",
            R["trust_ok"], trunc.get("side", "?"),
            " HITS-CORE" if trunc.get("hits_core") else "",
            rms_sig, rms_frac, inlier_pct, R.get("t_run", float("nan")),
        )

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
        csig_sigma=tuple(float(v) for v in R["csig_sigma"]),  # type: ignore[arg-type]
        trunc_side=str(trunc.get("side", "unknown")),
        trunc_hits_core=bool(trunc.get("hits_core", False)),
        msg=str(R.get("msg", "")),
    )


def display_curve(r: FringeFitResult, wl: np.ndarray):
    """(mid, half, phase) sampled on ``wl`` for the chart overlay, from a committed fit."""
    x = np.asarray(wl, dtype=float)
    Ud = fc.gauss(x, *r.pU)
    Ld = Ud - fc.gauss(x, *r.pLn)
    mid = 0.5 * (Ud + Ld)
    half = 0.5 * (Ud - Ld)
    phase = fc.phase_poly(np.asarray(r.csig, float), x - r.l0)
    return mid, half, phase
