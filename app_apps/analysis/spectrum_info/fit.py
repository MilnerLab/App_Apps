"""
Fit a measured spectrum to the envelope-bounded chirped-sinusoid model (M2.1).

Uses lmfit (least-squares). The oscillatory model has many local minima in the
phase/chirp parameters, so a reasonable initial guess matters; callers may pass an
``init`` (e.g. the previous fit, per the per-spectrum + rolling-stats scheme, D14).
Envelope parameters are initialised from the data when no ``init`` is given.
"""
from __future__ import annotations

import numpy as np

from app_apps.analysis.spectrum_info.model import (
    C_NM_THZ,
    SpectrumInfo,
    SpectrumParams,
    bounded_chirp_intensity,
    envelope_edges_thz,
)


def estimate_fringe_rate(
    wavelengths_nm: np.ndarray,
    intensities: np.ndarray,
    *,
    tau_min: float = 0.02,
    tau_max: float = 2.0,
) -> float:
    """Estimate the spectral fringe rate ``tau_ps`` by FFT (Fourier-transform interferometry).

    The fringe phase is ``phase0 + 2*pi*(tau*dnu + ...)``, so the spectrum oscillates in optical
    frequency at rate ``tau`` (cycles per THz == ps). We resample onto a uniform ``nu`` grid,
    flatten the Gaussian envelope, window, FFT, and take the dominant peak (parabolically
    interpolated) within ``[tau_min, tau_max]``. Seeding ``tau`` is what lets the cold fit lock
    onto the fringes; ``phase0`` then falls out of the least-squares fit. Returns 0.0 if there is
    no usable signal (the caller's fit will still run, just without a fringe-rate hint).
    """
    w = np.asarray(wavelengths_nm, dtype=float)
    y = np.clip(np.asarray(intensities, dtype=float), 0.0, None)
    if w.size < 8 or y.sum() <= 0:
        return 0.0
    nu = C_NM_THZ / w
    order = np.argsort(nu)
    nu, y = nu[order], y[order]
    nu_u = np.linspace(nu[0], nu[-1], nu.size)
    y = np.interp(nu_u, nu, y)
    # flatten the broad Gaussian envelope so the FFT sees a clean fringe peak (not envelope leakage)
    k = max(y.size // 8, 3)
    ker = np.exp(-0.5 * (np.arange(-3 * k, 3 * k + 1) / k) ** 2)
    ker /= ker.sum()
    env = np.convolve(y, ker, mode="same")
    env[env < 1e-9] = 1e-9
    yf = (y / env - 1.0)
    yf = (yf - yf.mean()) * np.hanning(yf.size)
    dnu = nu_u[1] - nu_u[0]
    freqs = np.fft.rfftfreq(yf.size, d=dnu)  # cycles per THz == ps
    amp = np.abs(np.fft.rfft(yf))
    band = np.where((freqs >= tau_min) & (freqs <= tau_max))[0]
    if band.size == 0:
        return 0.0
    i = int(band[np.argmax(amp[band])])
    delta = 0.0  # parabolic sub-bin interpolation of the peak
    if 0 < i < amp.size - 1:
        denom = amp[i - 1] - 2.0 * amp[i] + amp[i + 1]
        if denom != 0.0:
            delta = 0.5 * (amp[i - 1] - amp[i + 1]) / denom
    return float(freqs[i] + delta * (freqs[1] - freqs[0]))


def _intensity(
    wavelengths_nm,
    central_wavelength_nm,
    bandwidth_nm,
    amp_upper,
    amp_lower,
    phase0,
    tau_ps,
    g2,
    g3,
):
    """lmfit model function — thin wrapper around the forward model."""
    return bounded_chirp_intensity(
        wavelengths_nm,
        SpectrumParams(
            central_wavelength_nm=central_wavelength_nm,
            bandwidth_nm=bandwidth_nm,
            amp_upper=amp_upper,
            amp_lower=amp_lower,
            phase0=phase0,
            tau_ps=tau_ps,
            g2=g2,
            g3=g3,
        ),
    )


def estimate_envelope_init(
    wavelengths_nm: np.ndarray, intensities: np.ndarray
) -> SpectrumParams:
    """Heuristic init from the data: envelope/amplitudes + an FFT-seeded fringe rate ``tau_ps``.

    The fringe rate is seeded from :func:`estimate_fringe_rate` (FFT) so the cold least-squares
    fit can lock onto the fringes on the *first* spectrum; ``phase0``/``g2``/``g3`` are left at 0
    and recovered by the fit. (Previously ``tau`` started at 0, i.e. "no fringes", which made the
    cold fit diverge on any fringed spectrum.)
    """
    w = np.asarray(wavelengths_nm, dtype=float)
    y = np.clip(np.asarray(intensities, dtype=float), 0.0, None)
    total = y.sum()
    if total <= 0:
        center = float(w[len(w) // 2])
        fwhm = float((w[-1] - w[0]) / 4)
    else:
        center = float((w * y).sum() / total)
        var = float((y * (w - center) ** 2).sum() / total)
        fwhm = float(2.3548 * np.sqrt(max(var, 1e-12)))
    return SpectrumParams(
        central_wavelength_nm=center,
        bandwidth_nm=max(fwhm, 1e-3),
        amp_upper=float(y.max()),
        amp_lower=float(np.percentile(y, 5)),
        phase0=0.0,
        tau_ps=estimate_fringe_rate(w, y),
        g2=0.0,
        g3=0.0,
    )


def fit_spectrum(
    wavelengths_nm: np.ndarray,
    intensities: np.ndarray,
    *,
    init: SpectrumParams | None = None,
) -> SpectrumInfo:
    """Fit a spectrum and return a :class:`SpectrumInfo`."""
    import lmfit

    wavelengths_nm = np.asarray(wavelengths_nm, dtype=float)
    intensities = np.asarray(intensities, dtype=float)
    if init is None:
        init = estimate_envelope_init(wavelengths_nm, intensities)

    model = lmfit.Model(_intensity, independent_vars=["wavelengths_nm"])
    params = model.make_params(
        central_wavelength_nm=init.central_wavelength_nm,
        bandwidth_nm=init.bandwidth_nm,
        amp_upper=init.amp_upper,
        amp_lower=init.amp_lower,
        phase0=init.phase0,
        tau_ps=init.tau_ps,
        g2=init.g2,
        g3=init.g3,
    )
    params["bandwidth_nm"].set(min=1e-6)
    params["amp_upper"].set(min=0.0)
    params["amp_lower"].set(min=0.0)

    result = model.fit(intensities, params=params, wavelengths_nm=wavelengths_nm)

    v = result.best_values
    best = SpectrumParams(
        central_wavelength_nm=v["central_wavelength_nm"],
        bandwidth_nm=v["bandwidth_nm"],
        amp_upper=v["amp_upper"],
        amp_lower=v["amp_lower"],
        phase0=v["phase0"],
        tau_ps=v["tau_ps"],
        g2=v["g2"],
        g3=v["g3"],
    )
    nu0, nu_start, nu_end = envelope_edges_thz(best)
    residual = float(np.sqrt(np.mean((intensities - result.best_fit) ** 2)))

    return SpectrumInfo(
        central_wavelength_nm=best.central_wavelength_nm,
        bandwidth_nm=best.bandwidth_nm,
        amp_upper=best.amp_upper,
        amp_lower=best.amp_lower,
        phase0=best.phase0,
        tau_ps=best.tau_ps,
        g2=best.g2,
        g3=best.g3,
        nu0_thz=nu0,
        nu_start_thz=nu_start,
        nu_end_thz=nu_end,
        fit_residual=residual,
    )
