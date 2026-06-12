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
    SpectrumInfo,
    SpectrumParams,
    bounded_chirp_intensity,
    envelope_edges_thz,
)


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
    """Heuristic envelope/amplitude init from the data (phase/chirp left at 0)."""
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
        tau_ps=0.0,
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
