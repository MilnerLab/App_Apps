from __future__ import annotations

from collections import deque
from typing import Deque

import numpy as np

from base_core.math.models import Angle
from base_core.quantities.enums import Prefix
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    SpectralFitParams,
    StabilizationConfig,
)


class PhaseTracker:
    """
    Tracks the current phase by fitting a model to incoming spectra.

    Accepts raw numpy arrays (wavelengths in nm, intensities) directly.

    Mode is controlled by config.fit_all_params:
      - True:  fit_full() each spectrum; commit all fit params when batch residual is below threshold
      - False: fit_phase_only() each spectrum; commit only dphi0 when batch residual is below threshold

    current_phase is None until the first successful batch commit.
    """

    current_phase: Angle | None = None

    def __init__(self, start_config: StabilizationConfig) -> None:
        self._config: StabilizationConfig = start_config
        self._fits: Deque[SpectralFitParams] = deque(maxlen=self._config.avg_spectra)

    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray) -> bool:
        """Return True if the config was mutated (fit params updated)."""
        wl, inten = self._prepare(wavelengths_nm, intensities)

        fit = self._config.fit_full if self._config.fit_all_params else self._config.fit_phase_only
        self._fits.append(fit(wl, inten))

        if len(self._fits) < self._config.avg_spectra:
            return False

        averaged = SpectralFitParams.mean(self._fits)
        self._fits.clear()

        if averaged.residual >= self._config.residuals_threshold:
            return False

        if self._config.fit_all_params:
            self._config.copy_from(averaged)
        else:
            self._config.dphi0 = averaged.dphi0
            self._config.residual = averaged.residual

        self.current_phase = self._config.dphi0
        return True

    def _prepare(
        self, wavelengths_nm: np.ndarray, intensities: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        wl_min = self._config.wavelength_range.min.value(Prefix.NANO)
        wl_max = self._config.wavelength_range.max.value(Prefix.NANO)
        mask = (wavelengths_nm >= wl_min) & (wavelengths_nm <= wl_max)
        wl = wavelengths_nm[mask]
        inten = intensities[mask].astype(float)
        inten -= inten.min()
        max_val = inten.max()
        if max_val > 0:
            inten /= max_val
        return wl, inten
