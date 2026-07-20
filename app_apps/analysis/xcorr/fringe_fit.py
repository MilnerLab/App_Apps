"""Direct delay-domain fringe fit for XCORR traces (spec §8c.0).

An XCORR probe sweep is a Gaussian envelope carrying a chirped fringe, sampled in
**delay (ps)**. This module fits the phase of that fringe and hands back the cubic
phase coefficients plus their covariance. Everything is native to the delay axis:
there is no map into `fringe_core`'s wavelength domain, and nothing here imports it.

**Why not just call `fringe_core.analyze`** (the INV-6 decision, spec §8c.0): the
office script that does exactly that passes ``trunc_method="none", scanfree=True``,
which switches off every nm-calibrated part of that file. What is left over is
scale-covariant — fractions, thresholds on a normalised contrast, and a polynomial
in a shifted coordinate — so it needs no map. Meanwhile the map would have imported
`trust_at`'s thresholds, which are the *phase-stabilization loop's* budget in rad/nm.
Gating an XCORR readout on those would have been a boolean that looks principled
while measuring a different experiment's spec. So the surviving steps are ported here
in ps, and the pass/fail is replaced by a covariance the caller can propagate.

Pipeline, in order:

1. Upper-envelope Gaussian under the pinball loss (hugs the fringe crests).
2. Lower envelope from the negated residual → ``mid``/``half`` → normalised fringe
   ``n = (y - mid) / half``.
3. **Contrast crop** — closed-form threshold crossing on the gap Gaussian.
4. **Phase truncation** — refit after cropping where the Hilbert phase leaves a band
   around the fitted cubic. Two anti-circularity guards: the span may only ever
   *shrink*, and the fit runs at most twice.
5. Hilbert → unwrapped phase → seed; cubic refit on the raw counts, order by BIC
   over {2, 3}.

Pure numpy/scipy: no I/O, no Qt, no module-level mutable state. Never raises on a
bad trace — returns ``ok=False`` with a status string.

Ported from `fringe_core` (`gauss`, `pinball_loss`, `fit_upper_envelope`,
`fit_signal`, `coef_cov`, the trim seed and the BIC order choice), with the changes
§8c.0 called for: a moment-based envelope warm start instead of the nm-calibrated
``SIGMA_INIT = 4.0``, and scale-free convergence tolerances instead of absolute ones.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit, least_squares, minimize
from scipy.signal import hilbert

# --- envelope -----------------------------------------------------------------

#: Penalty ratio above:below the envelope fit. Higher hugs the crests tighter.
#: Carried over from `fringe_core.RATIO` — dimensionless, so it transfers as-is.
ENV_RATIO = 10.0
#: The pinball quantile that ratio implies (~0.91).
ENV_TAU = ENV_RATIO / (ENV_RATIO + 1.0)

#: Nelder-Mead tolerances for the pinball refinement. These are absolute in scipy,
#: which is why the envelope is fitted in **normalised coordinates** (time spanning
#: [-1, 1], amplitude divided by the trace's peak-to-peak) and converted back
#: afterwards. `fringe_core` instead left them absolute and made them meaningful by
#: rescaling every trace to a ~1e4 peak; that hack exists only to give an absolute
#: tolerance a scale, and normalising the fit removes the need for it (§8c.0).
ENV_XATOL = 1e-4
ENV_FATOL = 1e-4
ENV_MAXITER = 20000
#: `curve_fit` evaluation cap for the symmetric L2 warm start.
ENV_MAXFEV = 10000

# --- span selection -----------------------------------------------------------

#: Fraction of the peak envelope contrast kept as the core (`fringe_core`'s
#: TRUNC_THRESHOLD). A pure fraction of a fitted Gaussian's height — unit-free.
CONTRAST_THRESHOLD = 0.30

#: Phase truncation. The band is ``max(PHASE_BAND_MIN_RAD, NSIG * robust sigma)`` of
#: the Hilbert-vs-cubic phase residual; samples outside it, at the ends, are dropped.
#: The floor stops a very clean trace from cropping itself on its own noise.
PHASE_BAND_NSIG = 3.0
PHASE_BAND_MIN_RAD = 0.30
#: Refuse a phase-truncation crop that would throw away more than this fraction of
#: the contrast core. A crop that aggressive means the *fit* is wrong, not the span,
#: and shrinking the lever arm would only make the next fit worse.
PHASE_TRUNC_MAX_DROP = 0.40

#: Fewer core samples than this and there is nothing to fit.
MIN_CORE_PTS = 16
#: Fewer samples than this in the whole sweep and the crop stages are meaningless.
MIN_TRACE_PTS = 32

# --- phase fit ----------------------------------------------------------------

#: Phase-VALUE trim fraction for the polyfit seed: drop the top/bottom of the
#: Hilbert phase range before seeding, where the unwrap is least reliable.
SEED_PHASE_TRIM = 0.15
#: soft-L1 scale for the raw-count refit, as a fraction of the local half-amplitude.
#: Relative to the trace's own contrast, so it carries no units.
SIGNAL_LOSS_FRAC = 1.0
SIGNAL_MAXFEV = 6000

#: Below this the envelope has no gap and there are no fringes to fit.
DEAD_GAP_FRAC = 1e-3
DEAD_OSC_STD = 1e-6


# =============================================================================
# small shared pieces
# =============================================================================

def gauss(x: np.ndarray, a: float, mu: float, sigma: float, off: float) -> np.ndarray:
    return a * np.exp(-(x - mu) ** 2 / (2.0 * sigma ** 2)) + off


def phase_poly(c: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Cubic phase Φ(u) = c0 + c1·u + c2·u² + c3·u³, radians, u in ps."""
    c = np.asarray(c, float)
    return c[0] + c[1] * u + c[2] * u ** 2 + c[3] * u ** 3


def signal_model(c: np.ndarray, u: np.ndarray, mid: np.ndarray,
                 half: np.ndarray) -> np.ndarray:
    """The fringe as the fit sees it: envelope midline + contrast · cos Φ."""
    return mid + half * np.cos(phase_poly(c, u))


def fringe_freq_cyc_per_ps(c: np.ndarray, u: np.ndarray | float) -> np.ndarray:
    """Instantaneous fringe frequency dΦ/du / 2π, in cycles/ps.

    SIGNED. The overall sign of Φ is not observable (cos is even), so callers that
    want a frequency magnitude take ``abs()`` themselves — which is also why the
    sign costs nothing in the covariance propagation downstream.
    """
    c = np.asarray(c, float)
    u = np.asarray(u, float)
    return (c[1] + 2.0 * c[2] * u + 3.0 * c[3] * u ** 2) / (2.0 * np.pi)


def _bic(sse: float, k: int, n: int) -> float:
    """BIC from a residual sum of squares; the k·ln(n) term is what refuses a cubic
    the trace cannot support."""
    return n * np.log((float(sse) + 1e-12) / n) + k * np.log(n)


def _robust_sigma(v: np.ndarray) -> float:
    """MAD-based sigma. Used on the phase residual, where a handful of unwrap
    excursions at the ends would wreck a plain std — those excursions are the very
    thing being measured, so the estimator must not be moved by them."""
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    return 1.4826 * mad


# =============================================================================
# envelope
# =============================================================================

def _pinball_loss(p: np.ndarray, x: np.ndarray, y: np.ndarray) -> float:
    # residual > 0 means the data is above the fit, i.e. the fit sits below the
    # crests — penalise that at TAU so the solution rides the upper envelope.
    r = y - gauss(x, *p)
    return float(np.sum(np.where(r > 0, ENV_TAU * r, (ENV_TAU - 1.0) * r)))


def fit_upper_envelope(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Gaussian hugging the upper envelope of the fringes → ``(a, mu, sigma, off)``.

    Warm-started from a symmetric L2 fit, refined under the asymmetric pinball loss
    with Nelder-Mead. NM is kept for the refinement because the pinball kink is not
    differentiable and NM is unconditionally safe on it (`fringe_core` measured the
    quasi-Newton alternative at ~3 ms vs ~15 ms — free at this trace count).

    Two departures from `fringe_core.fit_upper_envelope`, both from §8c.0:

    * **The warm start is moment-based, not a constant.** `fringe_core` seeds sigma
      from ``SIGMA_INIT = 4.0`` nm, and that single number is most of why the affine
      map was proposed. The second moment of the baseline-subtracted trace is
      strictly better: it adapts to the scan range instead of assuming one.
    * **The solve is normalised.** Time is mapped to [-1, 1] and amplitude divided
      by the peak-to-peak, so NM's absolute ``xatol``/``fatol`` are meaningful for
      every parameter at once regardless of whether the trace peaks at 0.4 V or 1e4
      counts. `fringe_core` needed callers to rescale to a fixed peak for this
      reason; here it is internal and unconditional.

    There is no ``off_bounds``: that argument pins the offset to continuum measured
    outside the analysis window, and an XCORR sweep is the whole trace — there is no
    outside to measure.
    """
    x = np.asarray(x, float)
    y = np.asarray(y, float)

    # --- normalise ------------------------------------------------------------
    x_mid = 0.5 * (float(x[0]) + float(x[-1]))
    x_half = 0.5 * (float(x[-1]) - float(x[0])) or 1.0
    y_off = float(np.median(y))
    y_scale = float(np.ptp(y)) or 1.0
    xs = (x - x_mid) / x_half
    ys = (y - y_off) / y_scale

    # --- moment-based warm start (replaces SIGMA_INIT) ------------------------
    w = np.clip(ys, 0.0, None)
    total = float(np.sum(w))
    if total > 0:
        mu0 = float(np.sum(w * xs) / total)
        var0 = float(np.sum(w * (xs - mu0) ** 2) / total)
        sigma0 = float(np.sqrt(max(var0, 1e-6)))
    else:
        mu0, sigma0 = 0.0, 0.5
    p0 = [float(np.max(ys)), mu0, sigma0, 0.0]

    try:
        # The L2 warm start is an optimisation, not a requirement: on a flat or
        # fringe-free trace it cannot pin (a, mu, sigma, off) and says so, either by
        # raising or by warning that the covariance is unestimable. Both are fine —
        # fall back to the moment guess and let the pinball refinement do the work,
        # and downstream `no_fringes` catches the trace properly. The warning is
        # silenced rather than left to print, because it reaches the operator's log
        # as an alarming-looking message about a case that is already handled.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OptimizeWarning)
            p0, _ = curve_fit(gauss, xs, ys, p0=p0, maxfev=ENV_MAXFEV)
    except (RuntimeError, ValueError):
        pass

    res = minimize(_pinball_loss, np.asarray(p0, float), args=(xs, ys),
                   method="Nelder-Mead",
                   options={"maxiter": ENV_MAXITER, "maxfev": ENV_MAXITER,
                            "xatol": ENV_XATOL, "fatol": ENV_FATOL})
    a_s, mu_s, sigma_s, off_s = res.x

    # --- back to physical units ----------------------------------------------
    return np.array([a_s * y_scale,
                     x_mid + mu_s * x_half,
                     abs(sigma_s) * x_half,
                     y_off + off_s * y_scale], float)


# =============================================================================
# phase fit
# =============================================================================

def _fit_signal(u, y, mid, half, seed, q, f_scale):
    """Refine phase coeffs c0..cq on the RAW counts with the envelopes held fixed.

    Soft-L1 so a few outlying samples cannot pull the phase. Returns a full 4-vector
    with the unused high orders left at zero.
    """
    def resid(cc):
        cp = np.zeros(4)
        cp[:q + 1] = cc
        return signal_model(cp, u, mid, half) - y

    sol = least_squares(resid, np.asarray(seed, float)[:q + 1], loss="soft_l1",
                        f_scale=f_scale, max_nfev=SIGNAL_MAXFEV)
    cp = np.zeros(4)
    cp[:q + 1] = sol.x
    return cp


def _trim_seed_fit(u, phase, y, mid, half, f_scale, q, trim=SEED_PHASE_TRIM):
    """Phase-value trim → polyfit seed of degree q → raw-count refit.

    Dropping the top/bottom ``trim`` of the phase *range* (not of the index range)
    removes the plateaus where the Hilbert unwrap is least trustworthy, which are
    exactly the samples a least-squares polyfit would weight hardest.
    """
    lo, hi = float(phase.min()), float(phase.max())
    span = hi - lo + 1e-12
    keep = (phase >= lo + trim * span) & (phase <= hi - trim * span)
    if int(keep.sum()) < q + 2:
        keep = np.ones_like(phase, bool)
    cph = np.concatenate([np.polyfit(u[keep], phase[keep], q)[::-1], np.zeros(3 - q)])
    csig = _fit_signal(u, y, mid, half, cph, q, f_scale)
    sse = float(np.sum((signal_model(csig, u, mid, half) - y) ** 2))
    return csig, sse


def _fit_core(u, y, mid, half, n):
    """Hilbert seed + cubic refit on one core. Returns ``(csig, order, phase_h)``.

    Order is chosen by BIC over {2, 3}: the k·ln(n) penalty admits a cubic only when
    the trace earns it, and refuses an unidentifiable one — which is what keeps the
    covariance tight enough for the σ the caller reports. (q=1 is not offered: a
    pure carrier still needs c2 sampled to say so.)
    """
    phase_h = np.unwrap(np.angle(hilbert(n)))
    f_scale = SIGNAL_LOSS_FRAC * float(np.median(half)) + 1e-9
    cand = {q: _trim_seed_fit(u, phase_h, y, mid, half, f_scale, q) for q in (2, 3)}
    order = min(cand, key=lambda q: _bic(cand[q][1], q + 1, len(y)))
    return cand[order][0], order, phase_h


def coef_cov(u, half, csig, q, resid):
    """Covariance of the fitted phase coeffs, Gauss-Newton at the solution.

    ``model = mid + half·cos Φ`` ⇒ ``∂model/∂c_j = -half·sin(Φ)·u^j``, so
    ``cov = inv(JᵀJ)·SSE/dof``. Orders held at zero get zero variance.

    This is the whole point of the redesign. The residual cannot tell "fit the data
    and knows the chirp" from "fit the data and cannot pin the chirp" — a trace with
    too little lever arm still reconstructs beautifully. Only the covariance sees it,
    and it is what the reported σ(f₀) and σ(Δf) are built from.
    """
    n = len(u)
    k = q + 1
    if n <= k:
        return np.full((4, 4), np.inf)
    s = -half * np.sin(phase_poly(csig, u))
    J = np.stack([s * u ** j for j in range(k)], axis=1)
    try:
        JTJ_inv = np.linalg.inv(J.T @ J)
    except np.linalg.LinAlgError:
        return np.full((4, 4), np.inf)
    cov = np.zeros((4, 4))
    cov[:k, :k] = JTJ_inv * (float(np.sum(np.asarray(resid) ** 2)) / (n - k))
    return cov


# =============================================================================
# result
# =============================================================================

@dataclass(frozen=True)
class FringeFit:
    """One fitted XCORR fringe. Times in ps, phase coefficients in rad/ps^k.

    The phase is expressed about ``t0_ps`` — evaluate it at absolute time ``t`` with
    ``u = t - t0_ps``. The origin is the core's mean, which is what keeps the cubic
    design matrix conditioned; it is *not* the envelope centre.
    """

    ok: bool
    status: str
    #: Phase coefficients (c0, c1, c2, c3), rad. Orders above ``order`` are zero.
    csig: np.ndarray = field(default_factory=lambda: np.zeros(4))
    #: Covariance of ``csig``, same ordering.
    cov: np.ndarray = field(default_factory=lambda: np.full((4, 4), np.inf))
    #: Polynomial origin: ``u = t_ps - t0_ps``.
    t0_ps: float = float("nan")
    order: int = 0
    #: Upper-envelope Gaussian ``(a, mu, sigma, off)``; ``mu`` is the trace centre.
    p_upper: np.ndarray = field(default_factory=lambda: np.full(4, np.nan))
    #: The fitted core, after both crops.
    t_core_ps: np.ndarray = field(default_factory=lambda: np.empty(0))
    #: Normalised fringe on that core.
    n_core: np.ndarray = field(default_factory=lambda: np.empty(0))
    #: Envelope-stripped goodness of fit: cos Φ against the normalised fringe. A
    #: wrong phase cannot hide here the way it can in a raw-count R², which the
    #: fixed Gaussian envelope inflates for free.
    r2_fringe: float = float("nan")
    #: Median sample spacing of the *input* sweep, ps.
    dt_ps: float = float("nan")
    #: How many fit passes ran (1, or 2 when phase truncation cropped the span).
    n_passes: int = 0

    @property
    def t_mu_ps(self) -> float:
        """Centre of the upper Gaussian envelope — the trace's own time zero."""
        return float(self.p_upper[1])

    @property
    def span_ps(self) -> tuple[float, float]:
        """First and last time in the fitted core."""
        if self.t_core_ps.size == 0:
            return (float("nan"), float("nan"))
        return (float(self.t_core_ps[0]), float(self.t_core_ps[-1]))

    @property
    def nyquist_ghz(self) -> float:
        """Sampling Nyquist of the sweep, GHz. A fitted frequency approaching this
        is not measuring the fringe, it is aliasing."""
        return 1e3 / (2.0 * self.dt_ps)


def _fail(status: str, dt_ps: float = float("nan")) -> FringeFit:
    return FringeFit(ok=False, status=status, dt_ps=dt_ps)


# =============================================================================
# the pipeline
# =============================================================================

def _contrast_core(t, p_lower, threshold=CONTRAST_THRESHOLD):
    """Closed-form contrast crop: keep where the envelope gap exceeds a fraction of
    its own peak. The gap is a fitted Gaussian, so the crossing is analytic — no
    search, and no dependence on any phase model, which is exactly why this has to
    run before phase truncation rather than instead of it.
    """
    a, mu, sigma, off = p_lower
    max_gap = a + off
    min_gap = min(gauss(t[0], *p_lower), gauss(t[-1], *p_lower))
    level = min_gap + (max_gap - min_gap) * threshold
    arg = (level - off) / a if a != 0 else np.nan
    if np.isfinite(arg) and 0.0 < arg < 1.0:
        delta = abs(sigma) * np.sqrt(-2.0 * np.log(arg))
        return (t >= mu - delta) & (t <= mu + delta)
    return np.ones_like(t, bool)


def _phase_truncate(keep, u, phase_h, csig):
    """Crop the core to where the Hilbert phase tracks the fitted cubic.

    The contrast crop is blind to phase: it keeps whatever has visible contrast,
    including a tail whose fringe has gone non-sinusoidal or run past Nyquist. Those
    samples fit badly and, worse, drag c2/c3 — the coefficients the readout is made
    of. So after a first fit, measure the Hilbert-vs-cubic phase residual and keep
    the longest run around the envelope centre that stays inside a band.

    Two guards against the obvious circularity — cropping to where the model already
    agrees, then declaring agreement:

    * the returned span is a **subset** of the one passed in (the caller enforces the
      shrink-only rule by construction, since this only ever narrows ``keep``), and
    * a crop that would drop more than ``PHASE_TRUNC_MAX_DROP`` of the core is
      **refused** — that much disagreement means the fit is wrong, and cutting the
      lever arm would only make the refit worse.

    The caller runs it at most once, so the pipeline is capped at two fits total.
    Returns a new mask, or ``None`` to keep what we have.
    """
    resid = phase_h - phase_poly(csig, u)
    resid = resid - float(np.median(resid))           # the constant is not observable
    band = max(PHASE_BAND_MIN_RAD, PHASE_BAND_NSIG * _robust_sigma(resid))
    inside = np.abs(resid) <= band
    if inside.all():
        return None

    # Longest contiguous run of in-band samples, anchored on the strongest fringe
    # (the middle of the core) so an in-band run out in a dead tail cannot win.
    centre = len(inside) // 2
    if not inside[centre]:
        return None
    lo = centre
    while lo > 0 and inside[lo - 1]:
        lo -= 1
    hi = centre
    while hi < len(inside) - 1 and inside[hi + 1]:
        hi += 1

    n_keep = hi - lo + 1
    if n_keep < MIN_CORE_PTS:
        return None
    if n_keep < (1.0 - PHASE_TRUNC_MAX_DROP) * len(inside):
        return None
    if n_keep == len(inside):
        return None

    idx = np.flatnonzero(keep)[lo:hi + 1]
    out = np.zeros_like(keep)
    out[idx] = True
    return out


def fit_fringe(t_ps, y) -> FringeFit:
    """Fit one XCORR trace in the delay domain. Never raises.

    ``t_ps`` must be sorted ascending and near-uniformly sampled (the Hilbert
    transform requires it). ``y`` is the raw per-point signal — no rescaling needed.
    """
    t = np.asarray(t_ps, float)
    y = np.asarray(y, float)

    if t.size != y.size:
        return _fail("shape_mismatch")
    if t.size < MIN_TRACE_PTS:
        return _fail("too_few_points")
    if not (np.all(np.isfinite(t)) and np.all(np.isfinite(y))):
        return _fail("nonfinite")

    dt = float(np.median(np.diff(t)))
    if not np.isfinite(dt) or dt <= 0:
        return _fail("bad_time_axis")

    try:
        # --- 1/2. envelopes ---------------------------------------------------
        # Both are fitted on the FULL sweep: the upper Gaussian needs its wings to
        # pin mu/sigma, and narrowing the lower one's domain degenerates the gap.
        p_upper = fit_upper_envelope(t, y)
        p_lower = fit_upper_envelope(t, -(y - gauss(t, *p_upper)))

        upper = gauss(t, *p_upper)
        gap = gauss(t, *p_lower)
        lower = upper - gap
        mid_all = 0.5 * (upper + lower)
        half_all = 0.5 * (upper - lower)

        peak_gap = abs(p_lower[0] + p_lower[3])
        span = float(np.ptp(y)) + 1e-12
        detrended = y - upper
        if peak_gap < DEAD_GAP_FRAC * span or float(np.std(detrended)) < DEAD_OSC_STD * (span + 1):
            return _fail("no_fringes", dt)
        if not np.all(half_all > 0):
            return _fail("degenerate_envelope", dt)

        n_all = (y - mid_all) / half_all

        # --- 3a. contrast crop ------------------------------------------------
        keep = _contrast_core(t, p_lower)
        if int(np.count_nonzero(keep)) < MIN_CORE_PTS:
            return _fail("contrast_core_too_small", dt)

        # --- 3b/4/5. fit, phase-truncate, refit (at most twice) --------------
        n_passes = 0
        csig = order = phase_h = u = None
        for _ in range(2):
            t_c = t[keep]
            origin = float(np.mean(t_c))
            u = t_c - origin
            csig, order, phase_h = _fit_core(u, y[keep], mid_all[keep],
                                             half_all[keep], n_all[keep])
            n_passes += 1
            if n_passes == 2:
                break
            cropped = _phase_truncate(keep, u, phase_h, csig)
            if cropped is None:
                break
            keep = cropped

        t_core = t[keep]
        origin = float(np.mean(t_core))
        u = t_core - origin
        y_c, mid_c, half_c, n_c = y[keep], mid_all[keep], half_all[keep], n_all[keep]

        # --- covariance and fit quality --------------------------------------
        resid = signal_model(csig, u, mid_c, half_c) - y_c
        cov = coef_cov(u, half_c, csig, order, resid)

        n_model = np.cos(phase_poly(csig, u))
        ss_res = float(np.sum((n_c - n_model) ** 2))
        ss_tot = float(np.sum((n_c - float(np.mean(n_c))) ** 2)) + 1e-12
        r2_fringe = 1.0 - ss_res / ss_tot

        return FringeFit(
            ok=True,
            status="ok",
            csig=csig,
            cov=cov,
            t0_ps=origin,
            order=int(order),
            p_upper=p_upper,
            t_core_ps=t_core,
            n_core=n_c,
            r2_fringe=float(r2_fringe),
            dt_ps=dt,
            n_passes=n_passes,
        )
    except Exception as exc:                     # pragma: no cover - defensive
        # A fit failure greys one panel; it must never take a run down (spec §8c.0).
        return _fail(f"fit_error: {exc.__class__.__name__}", dt)
