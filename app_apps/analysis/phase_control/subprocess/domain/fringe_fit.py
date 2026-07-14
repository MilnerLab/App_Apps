"""Cubic-phase fringe fit — pure NumPy/scipy port of the standalone analysis in
``Data/20260709/spectrometer/plot_traces.py`` (matplotlib/glob/STFT stripped).

Pipeline per trace (raw counts, already windowed to the analysis band):
  1. asymmetric pinball-loss Gaussian fit of the upper envelope, and of the
     (upper-minus-lower) gap envelope;
  2. closed-form truncation to the high-visibility core;
  3. Hilbert transform -> unwrapped phase & instantaneous frequency;
  4. robust folded-chirp fit (single null, no search) to seed the phase;
  5. FINAL cubic (TOD) fit to the raw fringes with both envelopes held fixed.

The authoritative phase is the final cubic ``csig`` evaluated at a fixed
reference wavelength (``analyze_trace`` -> ``FringeFitResult.phase_at``), NOT the
intermediate Hilbert/folded fit.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import curve_fit, least_squares, minimize
from scipy.signal import hilbert


# --------------------------------------------------------------------------- #
# Tunables (module constants in the standalone script; per-config here).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FitTunables:
    ratio: float = 10.0          # pinball penalty ratio above:below (higher hugs crests)
    sigma_init: float = 4.0      # initial Gaussian sigma guess (nm) for the L2 warm start
    trunc_threshold: float = 0.25  # keep where gap >= min + THRESHOLD*(max-min)
    phase_loss_scale: float = 1.0  # soft-L1 scale (rad) for the folded-phase fit
    signal_loss_frac: float = 1.0  # soft-L1 scale as a fraction of local half-amplitude
    init_smooth_div: int = 50    # null-init smoothing sigma = max(N // this, 2)
    inlier_nsigma: float = 3.0   # inlier if |resid| < this * robust MAD scale
    # solver caps (rarely touched; kept out of the UI)
    fit_maxfev: int = 10_000     # curve_fit evaluation cap (warm start)
    fit_maxiter: int = 20_000    # L-BFGS-B iteration cap (pinball refinement)
    fit_ftol: float = 1e-4       # L-BFGS-B f-tolerance


@dataclass(frozen=True)
class FringeFitResult:
    """Outcome of one trace fit. ``accepted`` is a solver-success flag only; the
    caller applies its own quality gate on ``rms_sig`` / ``inlier_pct``."""
    accepted: bool
    pU: tuple[float, float, float, float]     # upper envelope Gaussian (a, mu, sigma, off)
    pLn: tuple[float, float, float, float]    # gap (U-L) Gaussian (a, mu, sigma, off)
    l0: float                                 # null / phase origin (nm)
    csig: tuple[float, float, float, float]   # cubic phase coeffs (c0, c1, c2, c3) in u=lambda-l0
    phase_ref: float                          # phase_poly(csig, lambda_ref - l0) [rad]
    rms_sig: float                            # raw-signal fit RMS (counts)
    inlier_pct: float                         # folded-phase inlier fraction (%)
    has_null: bool                            # null lies inside the truncated window

    def phase_at(self, lambda_ref_nm: float) -> float:
        """Cubic phase at a fixed reference wavelength (radians, unwrapped)."""
        return float(phase_poly(self.csig, lambda_ref_nm - self.l0))


def rejected() -> FringeFitResult:
    """A fit that failed to converge / had too little signal. NaN-filled."""
    nan4 = (float("nan"),) * 4
    return FringeFitResult(False, nan4, nan4, float("nan"), nan4,
                           float("nan"), float("inf"), 0.0, False)


# --------------------------------------------------------------------------- #
# Model primitives (identical math to the standalone script).
# --------------------------------------------------------------------------- #
def gauss(x: np.ndarray, a: float, mu: float, sigma: float, off: float) -> np.ndarray:
    return a * np.exp(-(x - mu) ** 2 / (2 * sigma ** 2)) + off


def _pinball_loss(p: np.ndarray, x: np.ndarray, y: np.ndarray, tau: float) -> float:
    r = y - gauss(x, *p)
    return float(np.sum(np.where(r > 0, tau * r, (tau - 1.0) * r)))


def _pinball_grad(p: np.ndarray, x: np.ndarray, y: np.ndarray, tau: float) -> np.ndarray:
    a, mu, sig, _off = p
    E = np.exp(-(x - mu) ** 2 / (2 * sig ** 2))
    r = y - (a * E + p[3])
    w = np.where(r > 0, tau, tau - 1.0)
    dg_da = E
    dg_dmu = a * E * (x - mu) / sig ** 2
    dg_dsig = a * E * (x - mu) ** 2 / sig ** 3
    return -np.array([np.sum(w * dg_da), np.sum(w * dg_dmu),
                      np.sum(w * dg_dsig), float(np.sum(w))])


def fit_upper_envelope(
    x: np.ndarray, y: np.ndarray, t: FitTunables,
    p0: tuple[float, float, float, float] | None = None,
) -> np.ndarray:
    """Symmetric L2 warm start, then asymmetric pinball refinement (L-BFGS-B with
    the analytic subgradient) so the Gaussian hugs the upper fringe envelope.

    ``p0`` seeds the L2 warm start from a prior fit (warm path); when None the
    seed is the data argmax (cold path)."""
    tau = t.ratio / (t.ratio + 1.0)
    if p0 is None:
        off0 = float(np.median(y))
        imax = int(np.argmax(y))
        p0 = [y[imax] - off0, x[imax], t.sigma_init, off0]
    p0, _ = curve_fit(gauss, x, y, p0=list(p0), maxfev=t.fit_maxfev)
    res = minimize(_pinball_loss, p0, args=(x, y, tau), method="L-BFGS-B",
                   jac=_pinball_grad,
                   options={"maxiter": t.fit_maxiter, "ftol": t.fit_ftol, "gtol": 1e-8})
    return res.x


def folded_phase(p: np.ndarray, l: np.ndarray) -> np.ndarray:
    """Hilbert-measured phase of a single-null linear chirp: |f| folding turns the
    signed parabola into a C1 kink at the null l0."""
    A, l0, C = p
    return A * (l - l0) * np.abs(l - l0) + C


def phase_poly(c: tuple[float, float, float, float] | np.ndarray, u: np.ndarray | float) -> np.ndarray | float:
    """Cubic (TOD) instantaneous phase in u = lambda - l0."""
    c0, c1, c2, c3 = c
    return c0 + c1 * u + c2 * u ** 2 + c3 * u ** 3


def signal_model(c: np.ndarray, u: np.ndarray, mid: np.ndarray, half: np.ndarray) -> np.ndarray:
    """Full raw-fringe model: two fixed envelopes carrying a cubic-phase cosine."""
    return mid + half * np.cos(phase_poly(c, u))


# --------------------------------------------------------------------------- #
# Full pipeline.
# --------------------------------------------------------------------------- #
def analyze_trace(
    wl: np.ndarray,
    intensity: np.ndarray,
    t: FitTunables,
    seed: FringeFitResult | None = None,
) -> FringeFitResult:
    """Fit one already-windowed trace. Returns a NaN-filled ``rejected()`` result
    on any solver failure or degenerate input rather than raising.

    ``seed`` selects the path:
      - None  -> COLD: envelopes from the data argmax, and a from-scratch null
        search (smoothed-|f| argmin) to seed the folded-chirp fit. Robust but the
        null init is the expensive/fragile part.
      - prior -> WARM: envelope + folded + final-cubic solvers are warm-started
        from the seed and the null search is SKIPPED (l0 origin taken from the
        seed). Same quality metrics as cold, so the caller's gate is uniform."""
    warm = seed is not None and seed.accepted
    try:
        x = np.asarray(wl, dtype=float)
        y = np.asarray(intensity, dtype=float)
        if x.size < 16:
            return rejected()

        # --- Envelopes: upper, and the (upper-lower) gap from the negated residual.
        pU = fit_upper_envelope(x, y, t, p0=seed.pU if warm else None)
        resid_env = y - gauss(x, *pU)
        pLn = fit_upper_envelope(x, -resid_env, t, p0=seed.pLn if warm else None)

        # --- Closed-form truncation to the high-visibility core.
        aLn, muLn, sLn, offLn = pLn
        max_diff = aLn + offLn
        min_diff = min(gauss(x[0], *pLn), gauss(x[-1], *pLn))
        level = min_diff + (max_diff - min_diff) * t.trunc_threshold
        arg = (level - offLn) / aLn if aLn != 0 else -1.0
        if 0.0 < arg < 1.0:
            delta = abs(sLn) * np.sqrt(-2.0 * np.log(arg))
            x_left, x_right = muLn - delta, muLn + delta
        else:
            x_left, x_right = x[0], x[-1]

        # --- Normalize fringes with both envelopes; oscillates ~[-1, 1].
        Ud = gauss(x, *pU)
        Ld = Ud - gauss(x, *pLn)
        mid = 0.5 * (Ud + Ld)
        half = 0.5 * (Ud - Ld)
        n = (y - mid) / half

        keep = (x >= x_left) & (x <= x_right)
        xk, nk, midk, halfk, yk = x[keep], n[keep], mid[keep], half[keep], y[keep]
        if xk.size < 16:
            return rejected()

        # --- Hilbert analytic signal -> phase & instantaneous frequency.
        dx = float(np.mean(np.diff(xk)))
        analytic = hilbert(nk)
        phase = np.unwrap(np.angle(analytic))
        f_inst = np.gradient(phase, dx) / (2 * np.pi)

        # --- Folded-chirp fit seed. COLD: from-scratch null search (smoothed-|f|
        #     argmin). WARM: reuse the seed's (A=c2, l0, C=c0) and skip the search.
        if warm:
            folded_init = [seed.csig[2], seed.l0, seed.csig[0]]
        else:
            absf_s = gaussian_filter1d(np.abs(f_inst), sigma=max(xk.size // t.init_smooth_div, 2))
            k0 = int(np.argmin(absf_s))
            l0_0, C0 = float(xk[k0]), float(phase[k0])
            denom = float(np.max((xk - l0_0) ** 2))
            A0 = (phase.max() - phase.min()) / denom if denom > 0 else 1.0
            folded_init = [A0, l0_0, C0]

        sol = least_squares(lambda p: folded_phase(p, xk) - phase, folded_init,
                            loss="soft_l1", f_scale=t.phase_loss_scale)
        A, l0, C = sol.x
        resid = phase - folded_phase(sol.x, xk)
        mad = 1.4826 * np.median(np.abs(resid)) + 1e-9
        inlier_pct = 100.0 * float((np.abs(resid) < t.inlier_nsigma * mad).mean())
        has_null = bool(xk[0] < l0 < xk[-1])

        # --- FINAL cubic (TOD) raw-signal fit; envelopes fixed, seeded from the
        #     folded quadratic (c2=A, c0=C, c1=c3=0). This is the authoritative fit.
        u = xk - l0
        f_scale_sig = t.signal_loss_frac * float(np.median(halfk)) + 1e-9
        cubic_init = list(seed.csig) if warm else [C, 0.0, A, 0.0]
        csig = least_squares(lambda c: signal_model(c, u, midk, halfk) - yk,
                             cubic_init, loss="soft_l1", f_scale=f_scale_sig).x
        resid_sig = yk - signal_model(csig, u, midk, halfk)
        rms_sig = float(np.sqrt(np.mean(resid_sig ** 2)))

        c_tuple = (float(csig[0]), float(csig[1]), float(csig[2]), float(csig[3]))
        return FringeFitResult(
            accepted=True,
            pU=tuple(float(v) for v in pU),          # type: ignore[arg-type]
            pLn=tuple(float(v) for v in pLn),        # type: ignore[arg-type]
            l0=float(l0),
            csig=c_tuple,
            phase_ref=float("nan"),                  # filled by caller via phase_at(lambda_ref)
            rms_sig=rms_sig,
            inlier_pct=inlier_pct,
            has_null=has_null,
        )
    except (RuntimeError, ValueError, np.linalg.LinAlgError):
        return rejected()


class SeedController:
    """Warm/cold seed policy for the per-shot fit loop (pure; no I/O, no framework).

    Holds the last *good* result as the warm seed. After ``redo_after_bad``
    consecutive bad fits it latches into forced-cold mode; the latch clears only
    on a subsequent good (necessarily cold) fit. A bad fit never overwrites the
    seed.

    Contract the caller must honour: call ``next_seed()`` once, run exactly one
    ``analyze_trace`` with it, then call ``record()`` exactly once with the gate
    verdict. That one-verdict-per-attempt rule is what guarantees the failure
    counter always advances, so forced-cold can never livelock on cold attempts
    that never reach a verdict.
    """

    def __init__(self, redo_after_bad: int) -> None:
        self._redo_after_bad = int(redo_after_bad)
        self._seed: FringeFitResult | None = None
        self._consecutive_bad = 0
        self._force_cold = False

    def next_seed(self) -> FringeFitResult | None:
        """Seed for the next fit: None (cold) if forced-cold or no seed yet."""
        if self._force_cold or self._seed is None:
            return None
        return self._seed

    def record(self, result: FringeFitResult, good: bool) -> None:
        """Feed back the gate verdict for the attempt seeded by ``next_seed()``."""
        if good:
            self._seed = result
            self._consecutive_bad = 0
            self._force_cold = False
        else:
            self._consecutive_bad += 1
            if self._consecutive_bad >= self._redo_after_bad:
                self._force_cold = True   # cleared only by a later good (cold) fit

    def reset(self) -> None:
        """Drop all state (e.g. on worker Start/Stop) — next fit is cold."""
        self._seed = None
        self._consecutive_bad = 0
        self._force_cold = False

    @property
    def forcing_cold(self) -> bool:
        return self._force_cold

    @property
    def consecutive_bad(self) -> int:
        return self._consecutive_bad


def display_curve(
    r: FringeFitResult, wl_grid: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reconstruct (mid, half, phase) on an arbitrary wavelength grid from a
    committed fit, so the chart overlay can draw mid + half*cos(phase) without
    re-fitting. ``phase`` is the cubic Phi(lambda)."""
    U = gauss(wl_grid, *r.pU)
    L = U - gauss(wl_grid, *r.pLn)
    mid = 0.5 * (U + L)
    half = 0.5 * (U - L)
    phase = phase_poly(r.csig, wl_grid - r.l0)
    return mid, half, np.asarray(phase)
