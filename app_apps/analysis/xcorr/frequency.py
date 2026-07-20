"""The XCORR frequency-analysis seam (spec §8c.4).

One finished probe sweep in, one :class:`FrequencyTrace` out. This is the *only*
module the UI talks to, and it is deliberately thin: navigation, the stage↔time axis
toggle and the grid-summary plots are all built against this dataclass, so they can
be developed and tested against a stub returning ``ok=False`` without the fit landing.

Since INV-6 the seam has **no external dependency at all** — the fit underneath is
:mod:`app_apps.analysis.xcorr.fringe_fit`, which is ps-native numpy/scipy. There is
no import of ``fringe_core``, no affine map into a wavelength domain, and therefore
no branch dependency (defect G22 does not apply here).

**What replaced the pass/fail boolean.** The original design gated the readout on
``fringe_core``'s ``shape_ok``, which is derived from thresholds carrying the
*phase-stabilization loop's* budget in rad/nm. Instead, ``f₀`` and ``Δf`` are linear
functionals of the fitted phase coefficients, so their uncertainties come straight
out of the fit covariance in closed form — reported as error bars rather than a
boolean. Three genuinely XCORR-specific gates survive as ``trusted``: the
envelope-stripped r², whether the readout window lies inside the fitted span (outside
it the cubic is extrapolating, which is exactly where a loose c2/c3 explodes), and
proximity to the sampling Nyquist.

Pure numpy/scipy — no Qt, no I/O. Runs off the Qt main thread (N1); every entry point
returns rather than raises, so a bad scan greys one panel and the run continues.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app_apps.analysis.xcorr import fringe_fit as ff

#: Speed of light, mm/ps.
C_MM_PER_PS = 0.299792458

#: Default width of the window the bandwidth is read across, in ps (R13, config C23).
#: An **operator input**, not the fitted envelope width: it states the part of the
#: pulse the experiment cares about. The fit's own sigma is reported separately and
#: never feeds this.
DEFAULT_FWHM_PS = 320.0

#: Trust gates. Deliberately loose, and deliberately module constants rather than
#: buried numbers: they are the one part of this file that wants calibrating against
#: the first real grid. Synthetic traces fit at r² 0.994–0.998, but a real sweep runs
#: much closer to Nyquist — the office script measured r² 0.61 at 137 GHz against a
#: 150 GHz Nyquist, and that trace was usable. So the r² floor is set where the
#: cosine model has clearly stopped explaining the fringe, not where it stops being
#: pretty.
R2_TRUST_MIN = 0.50
#: Fraction of the sampling Nyquist above which a fitted frequency is more likely
#: aliasing than signal.
NYQUIST_TRUST_FRACTION = 0.80


def probe_mm_to_ps(probe_mm, zero_mm: float = 0.0):
    """Stage position → delay. Double-pass retroreflector, so ``t = 2·(x - x₀)/c``."""
    return 2.0 * (np.asarray(probe_mm, float) - float(zero_mm)) / C_MM_PER_PS


@dataclass(frozen=True)
class FrequencyTrace:
    """Result of fitting one probe sweep. Times in ps, frequencies in GHz.

    ``ok`` means the fit produced numbers. ``trusted`` means they should be believed
    — a scan that is ``ok`` but not ``trusted`` is displayed greyed with its status
    rather than silently plotted or silently dropped.
    """

    ok: bool
    status: str
    #: Fitted core time axis, NOT yet offset. See :attr:`t_centred_ps`.
    t_ps: np.ndarray = field(default_factory=lambda: np.empty(0))
    #: |f| along that axis, GHz.
    f_ghz: np.ndarray = field(default_factory=lambda: np.empty(0))
    #: Centre of the upper Gaussian envelope — the trace's own time zero.
    t_mu_ps: float = float("nan")
    #: |f| at the envelope centre.
    f_central_ghz: float = float("nan")
    #: Its 1σ uncertainty, propagated from the phase-fit covariance.
    f_central_sigma_ghz: float = float("nan")
    #: |f(μ + W/2) − f(μ − W/2)|, the frequency swept across the readout window.
    bandwidth_ghz: float = float("nan")
    #: Its 1σ uncertainty. Far tighter than σ(f₀) in general — a difference across a
    #: fixed window cancels the constant phase term, which is the dominant error.
    bandwidth_sigma_ghz: float = float("nan")
    #: The readout window W (config C23), ps.
    fwhm_ps: float = DEFAULT_FWHM_PS
    #: Sampling Nyquist of the sweep, GHz — draw this on the frequency panel.
    nyquist_ghz: float = float("nan")
    #: Envelope-stripped goodness of fit.
    r2_fringe: float = float("nan")
    #: Fitted envelope width sigma, ps. Reported, never used in the readout.
    sigma_env_ps: float = float("nan")
    #: Phase-polynomial order the BIC chose (2 or 3).
    order: int = 0
    #: All three gates passed.
    trusted: bool = False
    #: Individual gates, so the UI can say *which* one failed.
    r2_ok: bool = False
    window_inside_ok: bool = False
    nyquist_ok: bool = False

    @property
    def t_centred_ps(self) -> np.ndarray:
        """Time axis with μ at zero — what a FINISHED scan is plotted on (R12).

        An in-flight scan has no fitted μ yet and is plotted uncentred; that is why
        the axis toggle takes the offset as an argument rather than reading it here.
        """
        return self.t_ps - self.t_mu_ps


def _fail(status: str, fwhm_ps: float, **kw) -> FrequencyTrace:
    return FrequencyTrace(ok=False, status=status, fwhm_ps=fwhm_ps, **kw)


def _sigma_from(cov: np.ndarray, grad: np.ndarray) -> float:
    """1σ of a linear functional ``gᵀc`` of the fitted coefficients."""
    var = float(grad @ np.asarray(cov, float) @ grad)
    if not np.isfinite(var) or var < 0.0:
        return float("nan")
    return float(np.sqrt(var))


def fit_sweep(probe_mm, v_mean_pos, *, fwhm_ps: float = DEFAULT_FWHM_PS,
              probe_zero_mm: float = 0.0) -> FrequencyTrace:
    """Fit one finished probe sweep. Never raises — returns ``ok=False`` instead.

    ``probe_mm`` is the stage position per point and ``v_mean_pos`` the corresponding
    reduced signal (the routine's ``float(t[t > 0].mean())`` per trace). Points may
    arrive in any order; they are sorted by delay here.
    """
    x = np.asarray(probe_mm, float)
    y = np.asarray(v_mean_pos, float)
    if x.size != y.size:
        return _fail("shape_mismatch", fwhm_ps)

    t = probe_mm_to_ps(x, probe_zero_mm)
    order = np.argsort(t)
    t, y = t[order], y[order]

    fit = ff.fit_fringe(t, y)
    if not fit.ok:
        return _fail(fit.status, fwhm_ps, nyquist_ghz=fit.nyquist_ghz)

    csig, cov, t0 = fit.csig, fit.cov, fit.t0_ps
    t_mu = fit.t_mu_ps

    # --- readout ------------------------------------------------------------
    # f in cycles/ps → GHz is a factor of 1e3. No inverse map, no amplitude
    # rescale: the fit was done in these units (§8c.0).
    def f_ghz_at(t_ps):
        return 1e3 * ff.fringe_freq_cyc_per_ps(csig, np.asarray(t_ps, float) - t0)

    def grad_at(u: float) -> np.ndarray:
        """∂f[GHz]/∂(c0,c1,c2,c3) at offset u — f is linear in the coefficients."""
        return 1e3 * np.array([0.0, 1.0, 2.0 * u, 3.0 * u ** 2]) / (2.0 * np.pi)

    u_mu = t_mu - t0
    f_central = abs(float(f_ghz_at(t_mu)))
    f_central_sigma = _sigma_from(cov, grad_at(u_mu))

    half_w = 0.5 * float(fwhm_ps)
    u_hi, u_lo = u_mu + half_w, u_mu - half_w
    bandwidth = abs(float(f_ghz_at(t_mu + half_w) - f_ghz_at(t_mu - half_w)))
    bandwidth_sigma = _sigma_from(cov, grad_at(u_hi) - grad_at(u_lo))

    # --- gates ---------------------------------------------------------------
    lo, hi = fit.span_ps
    window_inside = bool(lo <= t_mu - half_w and t_mu + half_w <= hi)
    r2_ok = bool(np.isfinite(fit.r2_fringe) and fit.r2_fringe >= R2_TRUST_MIN)
    nyq = fit.nyquist_ghz
    f_peak = f_central + 0.5 * bandwidth
    nyquist_ok = bool(np.isfinite(nyq) and f_peak <= NYQUIST_TRUST_FRACTION * nyq)

    failed = [name for name, ok in (("low_r2", r2_ok),
                                    ("window_outside_fit", window_inside),
                                    ("near_nyquist", nyquist_ok)) if not ok]
    trusted = not failed

    return FrequencyTrace(
        ok=True,
        status="ok" if trusted else "untrusted: " + ", ".join(failed),
        t_ps=fit.t_core_ps,
        f_ghz=np.abs(f_ghz_at(fit.t_core_ps)),
        t_mu_ps=t_mu,
        f_central_ghz=f_central,
        f_central_sigma_ghz=f_central_sigma,
        bandwidth_ghz=bandwidth,
        bandwidth_sigma_ghz=bandwidth_sigma,
        fwhm_ps=float(fwhm_ps),
        nyquist_ghz=nyq,
        r2_fringe=fit.r2_fringe,
        sigma_env_ps=float(fit.p_upper[2]),
        order=fit.order,
        trusted=trusted,
        r2_ok=r2_ok,
        window_inside_ok=window_inside,
        nyquist_ok=nyquist_ok,
    )


# --- summary-plot coordinates (R14) ------------------------------------------

def delta_t_ps(delay_base_mm: float) -> float:
    """Probe/pump delay of the sweep's setpoint, ps.

    The **base** value — before the grating-tracking correction ``slope·g +
    intercept`` is added. The correction exists to keep the pulses overlapped as the
    grating moves; the experiment's independent variable is what was asked for, not
    what the tracking then commanded. Both are on disk per group
    (``Setpoint.delay_base_mm``).
    """
    return 2.0 * float(delay_base_mm) / C_MM_PER_PS


def separation_mm(grating_mm: float, grating_zero_mm: float) -> float:
    """Grating separation L, relative to the operator-supplied zero (config C22)."""
    return float(grating_mm) - float(grating_zero_mm)
