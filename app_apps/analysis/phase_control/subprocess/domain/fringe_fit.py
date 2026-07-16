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

import logging
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import curve_fit, least_squares, minimize
from scipy.signal import hilbert

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Tunables (module constants in the standalone script; per-config here).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FitTunables:
    ratio: float = 10.0          # pinball penalty ratio above:below (higher hugs crests)
    sigma_init: float = 4.0      # initial Gaussian sigma guess (nm) for the L2 warm start
    trunc_threshold: float = 0.40  # keep where gap >= min + THRESHOLD*(max-min); higher =>
                                   # tighter crop. Raised 0.25 -> 0.40 after a harness sweep:
                                   # pass rate climbs monotonically as we crop tighter to a
                                   # flat plateau at 0.35-0.45 (~98.6% vs 97.7% at 0.25) -- the
                                   # low-SNR fringe wings hurt the phase fit more than their
                                   # extra lever-arm helps. Held out on fresh seeds and on the
                                   # three real traces (da17 null unmoved) it stays ahead.
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
) -> FringeFitResult:
    """Fit one already-windowed trace, always from scratch. Returns a NaN-filled
    ``rejected()`` result on any solver failure or degenerate input rather than raising.

    Every call is an independent COLD fit: envelopes from the data argmax, and a
    from-scratch null search (smoothed-|f| argmin) to seed the folded-chirp fit. There
    is NO warm-starting -- a fit is never biased by a previous frame's result, so each
    shot is reproducible in isolation. The null search is the expensive part; that cost
    is accepted as the price of a fresh, seed-independent fit on every shot."""
    try:
        x = np.asarray(wl, dtype=float)
        y = np.asarray(intensity, dtype=float)
        if x.size < 16:
            log.warning("FITDIAG rejected: only %d points in window", x.size)
            return rejected()

        # --- Envelopes: upper, and the (upper-lower) gap from the negated residual.
        pU = fit_upper_envelope(x, y, t)
        resid_env = y - gauss(x, *pU)
        pLn = fit_upper_envelope(x, -resid_env, t)

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
            log.warning("FITDIAG rejected: core has only %d points after truncation "
                        "(trunc_threshold=%.2f, window %.1f-%.1f nm)",
                        xk.size, t.trunc_threshold, float(x[0]), float(x[-1]))
            return rejected()

        # --- Hilbert analytic signal -> phase & instantaneous frequency.
        dx = float(np.mean(np.diff(xk)))
        analytic = hilbert(nk)
        phase = np.unwrap(np.angle(analytic))
        f_inst = np.gradient(phase, dx) / (2 * np.pi)

        # --- Folded-chirp fit seed: from-scratch null search (smoothed-|f| argmin).
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
        cubic_init = [C, 0.0, A, 0.0]
        csig = least_squares(lambda c: signal_model(c, u, midk, halfk) - yk,
                             cubic_init, loss="soft_l1", f_scale=f_scale_sig).x
        resid_sig = yk - signal_model(csig, u, midk, halfk)
        rms_sig = float(np.sqrt(np.mean(resid_sig ** 2)))

        # --- Diagnostic logging (INFO): the single most useful comparison is the DATA
        #     fringe frequency (from the Hilbert |f|) against the SEED carrier (which the
        #     folded model forces to 0) and the FINAL fitted frequency. If data_f is a few
        #     cyc/nm but the fit frequency is far from it (or ~0 near l0), the folded/zero-
        #     carrier seed has trapped the cos fit in a wrong basin -- the expected failure
        #     on a good, many-fringe, no-null trace. f_fit(u)=(c1+2 c2 u+3 c3 u^2)/2pi.
        if log.isEnabledFor(logging.WARNING):
            absf = np.abs(f_inst)
            d10, d50, d90 = (float(v) for v in np.percentile(absf, [10, 50, 90]))
            nfringe_data = float(d50 * (xk[-1] - xk[0]))     # ~ number of fringes in core

            def _f_fit(uu: float) -> float:
                return float((csig[1] + 2 * csig[2] * uu + 3 * csig[3] * uu ** 2) / (2 * np.pi))

            log.warning(
                "FITDIAG N=%d core=%d dx=%.4fnm span=%.1fnm | data_f=%.2f cyc/nm "
                "(p10-90 %.2f-%.2f, ~%.0f fringes) | l0=%.2f has_null=%s | "
                "SEED carrier c1=0 c2=A=%.4g | FIT c=[%.4g,%.4g,%.4g,%.4g] "
                "fit_f L/C/R=%.2f/%.2f/%.2f cyc/nm | rms=%.1f inl=%.0f%%",
                x.size, xk.size, dx, float(xk[-1] - xk[0]),
                d50, d10, d90, nfringe_data, l0, has_null, A,
                csig[0], csig[1], csig[2], csig[3],
                _f_fit(float(u[0])), _f_fit(float(np.median(u))), _f_fit(float(u[-1])),
                rms_sig, inlier_pct,
            )

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
    except (RuntimeError, ValueError, np.linalg.LinAlgError) as e:
        log.warning("FITDIAG rejected: %s: %s", type(e).__name__, e)
        return rejected()


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
