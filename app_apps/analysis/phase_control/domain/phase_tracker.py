from __future__ import annotations

from collections import deque
import inspect
from typing import Any, Deque

import lmfit
import numpy as np

from base_core.math.functions import cfg_projection_nu_equal_amplitudes_safe
from base_core.math.models import Angle
from base_core.quantities.enums import Prefix
from app_apps.analysis.phase_control.domain.phase_stabilization_config import StabilizationConfig, SpectralFitParams


class PhaseTracker:
    """
    Tracks the current phase by fitting a model to incoming spectra.

    Accepts raw numpy arrays (wavelengths in nm, intensities) directly —
    no intermediate domain objects.

    Workflow (per spectrum):
      - during initial phase, gather several full fits to build a good
        starting configuration (SpectralFitParams.mean)
      - once configured, only fit the phase parameter on subsequent spectra
      - if the residuals are low enough, accept the new phase as current
    """

    current_phase: Angle | None = None

    def __init__(self, start_config: StabilizationConfig) -> None:
        self._config: StabilizationConfig = start_config
        self._fits: Deque[SpectralFitParams] = deque(maxlen=self._config.avg_spectra)

    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray) -> None:
        wl, inten = self._prepare(wavelengths_nm, intensities)

        if len(self._fits) < self._config.avg_spectra and self.current_phase is None:
            self._fits.append(self._initialize_fit_parameters(wl, inten))
            return

        if self.current_phase is None:
            self._config.copy_from(SpectralFitParams.mean(self._fits))

        if len(self._fits) < self._config.avg_spectra:
            self._fits.append(self._fit_phase(wl, inten))
            self.current_phase = Angle(0)
        else:
            new_config = SpectralFitParams.mean(self._fits)
            self._fits.clear()
            if new_config.residual < self._config.residuals_threshold:
                self.current_phase = new_config.phase
                self._config.phase = new_config.phase
                self._config.residual = new_config.residual

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

    def _initialize_fit_parameters(
        self, wavelengths_nm: np.ndarray, intensities: np.ndarray
    ) -> SpectralFitParams:
        first_arg = self._get_first_arg_name()
        model = lmfit.Model(cfg_projection_nu_equal_amplitudes_safe, independent_vars=[first_arg])
        fit_kwargs: dict[str, Any] = self._config.to_fit_kwargs(cfg_projection_nu_equal_amplitudes_safe)
        params = model.make_params(**fit_kwargs)
        params["a_R_THz_per_ps"].set(vary=False)
        if not self._config.has_acceleration:
            self._config.a_L_THz_per_ps = self._config.a_R_THz_per_ps
        result = model.fit(
            intensities,
            params=params,
            **{first_arg: wavelengths_nm},
            max_nfev=int(1_000_000),
        )
        return SpectralFitParams.from_fit_result(self._config, result)

    def _fit_phase(
        self, wavelengths_nm: np.ndarray, intensities: np.ndarray
    ) -> SpectralFitParams:
        first_arg = self._get_first_arg_name()
        model = lmfit.Model(cfg_projection_nu_equal_amplitudes_safe, independent_vars=[first_arg])
        params = model.make_params(**self._config.to_fit_kwargs(cfg_projection_nu_equal_amplitudes_safe))
        for name, par in params.items():
            par.vary = (name == "phase")
        result = model.fit(
            intensities,
            params=params,
            **{first_arg: wavelengths_nm},
        )
        return SpectralFitParams.from_fit_result(self._config, result)

    @staticmethod
    def _get_first_arg_name() -> str:
        sig = inspect.signature(cfg_projection_nu_equal_amplitudes_safe)
        return next(iter(sig.parameters))
