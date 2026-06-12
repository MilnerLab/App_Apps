"""
Cross-correlation (XCORR) and the wavelength↔probe-delay calibration (M3 / D8 / D15).

Physics recap (D8): the XCORR trace is the **same envelope-bounded chirped sinusoid**
as the spectrometer trace, but intensity-vs-time-within-pulse (from a probe-delay
scan) rather than intensity-vs-wavelength. Matching the same sinusoid across the two
abscissae yields the **wavelength↔probe-delay** map. This is a characterization tool
(per grating/delay-stage combination), not a real-time control input.

This module provides the unambiguous primitives:
- :func:`cross_correlate` — integer-lag cross-correlation (alignment / delay estimate).
- :class:`WavelengthDelayCalibration` — the stored wavelength↔delay table with
  interpolation both ways.

⚠️ **[PHYSICS-CONFIRM]** Deriving the (wavelength, delay) pairs by matching the two
fitted bounded sinusoids is the lab-convention-dependent step; this module takes the
paired arrays as input and owns the unambiguous correlation + table machinery.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def cross_correlate(a: np.ndarray, b: np.ndarray) -> int:
    """Return the integer lag (in samples) that best aligns ``b`` onto ``a``.

    The returned ``lag`` is defined so that ``a[n] ≈ b[n - lag]`` — i.e. a positive
    lag means ``b`` is shifted *later* than ``a``. Both inputs are mean-subtracted
    first so a constant offset doesn't dominate.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    a = a - a.mean()
    b = b - b.mean()
    corr = np.correlate(a, b, mode="full")
    return int(np.argmax(corr) - (len(b) - 1))


def lag_to_delay_ps(lag_samples: int, sample_dt_ps: float) -> float:
    """Convert an integer sample lag to a delay in ps."""
    return float(lag_samples) * float(sample_dt_ps)


@dataclass(frozen=True)
class WavelengthDelayCalibration:
    """A wavelength↔probe-delay calibration for one grating/delay-stage combination."""

    created_utc: str
    grating_stage: str
    grating_position: float
    delay_stage: str
    delay_position: float
    wavelengths_nm: np.ndarray   # ascending
    delays_ps: np.ndarray        # corresponding probe delays

    def __post_init__(self) -> None:
        wl = np.asarray(self.wavelengths_nm, dtype=float)
        dl = np.asarray(self.delays_ps, dtype=float)
        if wl.shape != dl.shape:
            raise ValueError("wavelengths_nm and delays_ps must have the same shape")
        if wl.size < 2:
            raise ValueError("need at least two points for a calibration")
        # store sorted-by-wavelength so interpolation is well-defined
        order = np.argsort(wl)
        object.__setattr__(self, "wavelengths_nm", wl[order])
        object.__setattr__(self, "delays_ps", dl[order])

    @property
    def combination(self) -> str:
        """A stable key for the grating/delay-stage combination."""
        return (
            f"{self.grating_stage}@{self.grating_position:g}"
            f"__{self.delay_stage}@{self.delay_position:g}"
        )

    def wavelength_to_delay(self, wavelength_nm: float | np.ndarray) -> np.ndarray:
        return np.interp(wavelength_nm, self.wavelengths_nm, self.delays_ps)

    def delay_to_wavelength(self, delay_ps: float | np.ndarray) -> np.ndarray:
        order = np.argsort(self.delays_ps)
        return np.interp(delay_ps, self.delays_ps[order], self.wavelengths_nm[order])
