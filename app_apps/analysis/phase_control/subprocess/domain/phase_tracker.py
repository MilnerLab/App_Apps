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

    Workflow (per spectrum):
      - during the init phase, accumulate avg_spectra full fits privately,
        then commit the averaged envelope+chirp params to the config once
      - afterwards, collect avg_spectra phase-only fits per batch, and commit
        only dphi0 + residual when the batch residual is below threshold
    """

    current_phase: Angle | None = None

    def __init__(self, start_config: StabilizationConfig) -> None:
        self._config: StabilizationConfig = start_config
        self._fits: Deque[SpectralFitParams] = deque(maxlen=self._config.avg_spectra)

    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray) -> bool:
        """Return True if the config was mutated (fit params updated)."""
        wl, inten = self._prepare(wavelengths_nm, intensities)
        config_changed = False

        if len(self._fits) < self._config.avg_spectra and self.current_phase is None:
            self._fits.append(self._config.fit_full(wl, inten))
            return False

        if self.current_phase is None:
            self._config.copy_from(SpectralFitParams.mean(self._fits))
            config_changed = True

        if len(self._fits) < self._config.avg_spectra:
            self._fits.append(self._config.fit_phase_only(wl, inten))
            self.current_phase = Angle(0)
        else:
            averaged = SpectralFitParams.mean(self._fits)
            self._fits.clear()
            if averaged.residual < self._config.residuals_threshold:
                self.current_phase = averaged.dphi0
                self._config.dphi0 = averaged.dphi0
                self._config.residual = averaged.residual
                config_changed = True

        return config_changed

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
