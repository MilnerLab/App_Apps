"""
Synthetic spectrum generator (M2.0 / D9).

Used to develop and unit-test the analysis without hardware, and as ground truth for
the fitter (generate with known params → fit → recover them). Also the basis for the
oscilloscope mock (same envelope-bounded-sinusoid shape vs a time abscissa).
"""
from __future__ import annotations

import numpy as np

from app_apps.analysis.spectrum_info.model import SpectrumParams, bounded_chirp_intensity


def wavelength_grid(start_nm: float, stop_nm: float, n: int = 3648) -> np.ndarray:
    """A uniform wavelength grid (default = SPM-002 pixel count)."""
    return np.linspace(start_nm, stop_nm, n)


def synthetic_spectrum(
    wavelengths_nm: np.ndarray,
    params: SpectrumParams,
    *,
    noise: float = 0.0,
    seed: int | None = None,
) -> np.ndarray:
    """Generate a noisy synthetic spectrum from the forward model.

    ``noise`` is the standard deviation of additive Gaussian noise (in the same units
    as the envelope amplitudes). Intensities are clipped at 0 (non-negative counts).
    """
    clean = bounded_chirp_intensity(wavelengths_nm, params)
    if noise > 0.0:
        rng = np.random.default_rng(seed)
        clean = clean + rng.normal(0.0, noise, size=clean.shape)
    return np.clip(clean, 0.0, None)
