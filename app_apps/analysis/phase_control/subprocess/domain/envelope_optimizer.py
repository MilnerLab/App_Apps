from __future__ import annotations

import numpy as np

from base_core.quantities.enums import Prefix
from app_apps.analysis.phase_control.subprocess.domain.envelope_config import EnvelopeConfig, EnvelopeMode
from app_apps.analysis.phase_control.subprocess.domain.phase_corrector import CorrectionResult
from base_core.math.models import Angle


class EnvelopeOptimizer:
    """
    Fixed-step hill-climb on a spectrum envelope metric.

    Keeps direction (+1/-1) and flips it whenever the metric stops improving.
    Returns a CorrectionResult so it shares the same output type as PhaseCorrector.
    """

    def __init__(self, config: EnvelopeConfig) -> None:
        self._config = config
        self._last_metric: float | None = None
        self._direction = 1

    def reset(self) -> None:
        self._last_metric = None
        self._direction = 1

    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray) -> CorrectionResult | None:
        wl_min = self._config.wavelength_range.min.value(Prefix.NANO)
        wl_max = self._config.wavelength_range.max.value(Prefix.NANO)
        mask = (wavelengths_nm >= wl_min) & (wavelengths_nm <= wl_max)
        y = intensities[mask].astype(float)

        if y.size == 0 or not np.all(np.isfinite(y)):
            return None

        y_s = self._smooth(y)
        metric = float(np.max(y_s))

        if self._last_metric is not None and not self._is_improved(metric):
            self._direction *= -1

        self._last_metric = metric

        angle = Angle(self._direction * float(self._config.step_angle))
        return CorrectionResult(angle=angle, sign=self._direction)

    def _smooth(self, y: np.ndarray) -> np.ndarray:
        w = int(self._config.smooth_window)
        if w <= 1 or y.size < w:
            return y
        return np.convolve(y, np.ones(w) / w, mode="same")

    def _is_improved(self, current: float) -> bool:
        if self._config.mode == EnvelopeMode.MAXIMIZE:
            return current > self._last_metric
        return current < self._last_metric
