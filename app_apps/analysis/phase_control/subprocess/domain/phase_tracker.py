from __future__ import annotations

import logging
from collections import deque
from typing import Deque

import numpy as np

from app_apps.io.spectrometer.domain.helpers import normalize_spectrum
from base_core.math.models import Angle
from base_core.quantities.enums import Prefix
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    SpectralFitParams,
    StabilizationConfig,
)

log = logging.getLogger(__name__)




class PhaseTracker:
    """
    Tracks the current phase by fitting a model to incoming spectra.

    Accepts raw numpy arrays (wavelengths in nm, intensities) directly.

    Mode is controlled by config.fit_all_params:
      - True:  fit_full() each spectrum; commit all fit params when batch residual is below threshold
      - False: fit_phase_only() each spectrum; commit only theta0 when batch residual is below threshold

    current_phase is None until the first successful batch commit.
    """

    current_phase: Angle | None = None

    def __init__(self, start_config: StabilizationConfig) -> None:
        self._config: StabilizationConfig = start_config
        self._fits: Deque[SpectralFitParams] = deque(maxlen=self._config.avg_spectra)

    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray) -> bool:
        """Return True if the config was mutated (fit params updated)."""
        wl, inten = self._prepare(wavelengths_nm, intensities)

        fit = self._config.fit(wl, inten)
        self._fits.append(fit)
        log.info(
            "PhaseTracker: fit residual=%.4g, theta0=%.3f deg  (batch %d/%d)",
            fit.residual, fit.theta0.Deg, len(self._fits), self._config.avg_spectra,
        )

        if len(self._fits) < self._config.avg_spectra:
            return False

        averaged = type(self._config.params).mean(self._fits)
        self._fits.clear()

        if averaged.residual >= self._config.residuals_threshold:
            log.info(
                "PhaseTracker: batch full -> averaged residual=%.4g >= threshold %s "
                "-> REJECT batch, current_phase stays %s (raise residuals_threshold or "
                "improve the fit to let it commit)",
                averaged.residual, self._config.residuals_threshold,
                f"{self.current_phase.Deg:.3f} deg" if self.current_phase is not None else "None",
            )
            return False

        if self._config.fit_all_params:
            self._config.params.copy_from(averaged)
        else:
            self._config.params.theta0 = averaged.theta0
            self._config.params.residual = averaged.residual

        self.current_phase = self._config.params.theta0
        log.info(
            "PhaseTracker: batch full -> averaged residual=%.4g < threshold %s -> COMMIT, "
            "current_phase=%.3f deg",
            averaged.residual, self._config.residuals_threshold, self.current_phase.Deg,
        )
        return True

    def _prepare(
        self, wavelengths_nm: np.ndarray, intensities: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        wl_min = self._config.wavelength_range.min.value(Prefix.NANO)
        wl_max = self._config.wavelength_range.max.value(Prefix.NANO)
        return normalize_spectrum(wavelengths_nm, intensities, wl_min, wl_max)
