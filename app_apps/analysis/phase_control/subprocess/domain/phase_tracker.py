from __future__ import annotations

import logging
import math
import time

import numpy as np

from base_core.math.models import Angle
from base_core.quantities.enums import Prefix
from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import (
    SeedController,
    analyze_trace,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    StabilizationConfig,
)

log = logging.getLogger(__name__)

_TWO_PI = 2.0 * math.pi


class PhaseTracker:
    """Per-shot fringe-phase tracker.

    Each spectrum is windowed and fit by ``fringe_fit.analyze_trace`` (warm-started
    from the previous good fit via a ``SeedController``, cold when forced). A fit
    that passes ``config.accepts`` commits its outputs into ``config.params`` (for
    the overlay) and sets ``current_phase`` = cubic phase at ``lambda_ref`` mod 2pi.

    ``current_phase`` is None until the first accepted fit, then holds the last
    committed value. ``update`` returns True only when a fresh fit committed.
    """

    current_phase: Angle | None = None

    def __init__(self, config: StabilizationConfig) -> None:
        self._config = config
        self._seeds = SeedController(config.redo_after_bad)

    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray,
               skipped: int = 0) -> bool:
        """Fit one spectrum. ``skipped`` = frames coalesced away since the last
        fit (drop-stale), reported in the log line. Returns True on a fresh commit."""
        wl, inten = self._window(wavelengths_nm, intensities)

        seed = self._seeds.next_seed()
        cold = seed is None
        was_forcing = self._seeds.forcing_cold

        t0 = time.perf_counter()
        result = analyze_trace(wl, inten, self._config.params.tunables(), seed=seed)
        ms = (time.perf_counter() - t0) * 1e3

        good = self._config.accepts(result)
        self._seeds.record(result, good)

        if good:
            lam_ref = self._config.params.lambda_ref.value(Prefix.NANO)
            phase_ref = result.phase_at(lam_ref)
            self._config.params.commit(result, phase_ref)
            self.current_phase = Angle(phase_ref % _TWO_PI)
            log.info("fit %s ok  phi=%.3frad rms=%.0f inl=%.0f%% skip=%d %.0fms",
                     "cold" if cold else "warm", phase_ref % _TWO_PI,
                     result.rms_sig, result.inlier_pct, skipped, ms)
            return True

        rms = "inf" if not np.isfinite(result.rms_sig) else f"{result.rms_sig:.0f}"
        newly_cold = self._seeds.forcing_cold and not was_forcing
        log.info("fit %s REJECT rms=%s inl=%.0f%% skip=%d %.0fms%s",
                 "cold" if cold else "warm", rms, result.inlier_pct, skipped, ms,
                 f" -> cold ({self._seeds.consecutive_bad} bad in a row)" if newly_cold else "")
        return False

    def _window(self, wavelengths_nm: np.ndarray,
                intensities: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        wl = np.asarray(wavelengths_nm, dtype=float)
        inten = np.asarray(intensities, dtype=float)
        lo = self._config.wavelength_range.min.value(Prefix.NANO)
        hi = self._config.wavelength_range.max.value(Prefix.NANO)
        mask = (wl >= lo) & (wl <= hi)
        return wl[mask], inten[mask]
