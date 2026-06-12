"""
Forward model for the usCFG spectrometer trace (M2.1 / D13 / Q6b).

The trace is an **envelope-bounded sinusoid**:

    I(ν) = E_lo(ν) + (E_up(ν) - E_lo(ν)) * 0.5 * (1 + cos Φ(ν))

- ``E_up`` / ``E_lo`` are independent Gaussian envelopes (same centre/width, separate
  amplitudes). The **lower** envelope amplitude is the QWP ellipticity metric
  (minimise → 0); a flat lower envelope is NOT assumed (unlike the legacy
  ``*_projection`` functions, which force ``E_lo = baseline``).
- ``Φ(ν)`` is the fringe phase, a **cubic** polynomial in optical frequency
  ``dν = ν - ν0`` (cubic spectral phase ↔ quadratic instantaneous-frequency-vs-time,
  the single system chirp; per Q6b):

      Φ(ν) = phase0 + 2π ( tau_ps·dν + g2·dν² + g3·dν³ )

  where ``tau_ps`` is the linear (delay) term, ``g2`` the quadratic spectral phase
  (↔ grating chirp-rate ``f1``), and ``g3`` the cubic spectral phase (↔ TOD ``f2``).

This module is self-contained (numpy only). The spectral coefficients are what the
spectrometer measures directly; conversion to physical temporal chirp ``f(t)`` is a
separate, clearly-flagged step (see :func:`temporal_chirp` and its docstring).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Speed of light expressed so that nu[THz] = C_NM_THZ / lambda[nm].
C_NM_THZ = 299_792.458
_FWHM_TO_SIGMA = 1.0 / np.sqrt(8.0 * np.log(2.0))


@dataclass(frozen=True)
class SpectrumParams:
    """Parameters of the forward model (also the fit's free parameters)."""

    central_wavelength_nm: float
    bandwidth_nm: float          # FWHM of the Gaussian envelope, in nm
    amp_upper: float             # upper-envelope amplitude
    amp_lower: float             # lower-envelope amplitude (QWP ellipticity metric)
    phase0: float                # initial fringe phase [rad]
    tau_ps: float                # linear spectral phase (delay) [ps]
    g2: float                    # quadratic spectral phase [ps^2]  (~ chirp rate f1)
    g3: float                    # cubic spectral phase   [ps^3]  (~ TOD f2)


@dataclass(frozen=True)
class SpectrumInfo:
    """Result of fitting one spectrum — the published fit parameters (D13)."""

    central_wavelength_nm: float
    bandwidth_nm: float
    amp_upper: float
    amp_lower: float             # lower-envelope metric (QWP loop input)
    phase0: float                # initial/instantaneous fringe phase [rad]
    tau_ps: float
    g2: float
    g3: float
    nu0_thz: float               # central optical frequency
    nu_start_thz: float          # frequency at the blue envelope edge (λ0 - 2σ)
    nu_end_thz: float            # frequency at the red envelope edge  (λ0 + 2σ)
    fit_residual: float          # RMS residual of the fit

    @property
    def lower_envelope_metric(self) -> float:
        """QWP ellipticity metric — minimise this (D19)."""
        return self.amp_lower


def nu_thz(wavelength_nm: np.ndarray | float) -> np.ndarray | float:
    """Optical frequency [THz] from wavelength [nm]."""
    return C_NM_THZ / np.asarray(wavelength_nm, dtype=float)


def gaussian_envelope(
    wavelengths_nm: np.ndarray, center_nm: float, fwhm_nm: float
) -> np.ndarray:
    sigma = max(fwhm_nm * _FWHM_TO_SIGMA, 1e-12)
    return np.exp(-0.5 * ((wavelengths_nm - center_nm) / sigma) ** 2)


def fringe_phase(wavelengths_nm: np.ndarray, p: SpectrumParams) -> np.ndarray:
    """The cubic spectral fringe phase Φ(ν) over a wavelength grid."""
    nu = nu_thz(wavelengths_nm)
    dnu = nu - (C_NM_THZ / p.central_wavelength_nm)
    return p.phase0 + 2.0 * np.pi * (
        p.tau_ps * dnu + p.g2 * dnu**2 + p.g3 * dnu**3
    )


def bounded_chirp_intensity(
    wavelengths_nm: np.ndarray, p: SpectrumParams
) -> np.ndarray:
    """Evaluate the envelope-bounded chirped-sinusoid intensity on a wavelength grid."""
    wavelengths_nm = np.asarray(wavelengths_nm, dtype=float)
    g = gaussian_envelope(wavelengths_nm, p.central_wavelength_nm, p.bandwidth_nm)
    e_up = p.amp_upper * g
    e_lo = p.amp_lower * g
    modulation = 0.5 * (1.0 + np.cos(fringe_phase(wavelengths_nm, p)))
    return e_lo + (e_up - e_lo) * modulation


def envelope_edges_thz(p: SpectrumParams) -> tuple[float, float, float]:
    """Return (nu0, nu_start_blue, nu_end_red) at the ±2σ envelope edges."""
    sigma = p.bandwidth_nm * _FWHM_TO_SIGMA
    nu0 = float(C_NM_THZ / p.central_wavelength_nm)
    nu_blue = float(C_NM_THZ / max(p.central_wavelength_nm - 2 * sigma, 1e-9))
    nu_red = float(C_NM_THZ / (p.central_wavelength_nm + 2 * sigma))
    return nu0, nu_blue, nu_red


def temporal_chirp(p: SpectrumParams) -> tuple[float, float, float]:
    """Best-effort conversion spectral-phase → temporal chirp ``f(t) = [f0, f1, f2]``.

    ⚠️ **[PHYSICS-CONFIRM]** Leading-order mapping only. ``f0`` is the central
    frequency; the linear chirp rate ``f1`` and TOD ``f2`` are proportional to the
    quadratic/cubic spectral-phase coefficients ``g2``/``g3`` respectively, but the
    exact proportionality constants depend on the chirp convention the lab uses
    (to be confirmed). Until then, treat the *spectral* coefficients (``g2``/``g3``)
    in :class:`SpectrumInfo` as the source of truth for control; this helper is a
    convenience that returns the leading-order interpretation.
    """
    f0 = float(C_NM_THZ / p.central_wavelength_nm)
    # Leading order: f1 ∝ g2, f2 ∝ g3. Proportionality TBD (see docstring).
    f1 = float(p.g2)
    f2 = float(p.g3)
    return f0, f1, f2
