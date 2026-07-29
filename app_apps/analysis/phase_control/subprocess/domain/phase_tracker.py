from __future__ import annotations

import logging
import math
import time

import numpy as np

from base_core.math.models import Angle
from base_core.quantities.enums import Prefix
from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import (
    ClipCache,
    ReferencePolicy,
    analyze_trace,
    baseline_anchor,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    StabilizationConfig,
)

log = logging.getLogger(__name__)

_TWO_PI = 2.0 * math.pi


class PhaseTracker:
    """Per-shot fringe-phase tracker.

    Each spectrum is windowed and fit fresh by ``fringe_fit.analyze_trace`` -- a cold,
    seed-independent fit on every shot (NO warm-starting). A fit that passes
    ``config.accepts`` commits its outputs into ``config.params`` (for the overlay) and
    sets ``current_phase`` = cubic phase at ``lambda_ref`` mod 2pi.

    ``current_phase`` is None until the first accepted fit, then holds the last
    committed value. ``update`` returns True only when a fresh fit committed.

    Two things are carried across frames (only these two; the fit itself stays cold and
    seed-independent -- neither seeds the optimiser):
      * ``_ref_policy`` -- phase-reference hysteresis, so a loop locked to one wavelength
        cannot chatter between two when a clip sits near the core.
      * ``_clip_cache`` -- the knife edge in WAVELENGTH, so a clipped frame need not re-run
        the recovery scan to rediscover an edge that has not moved. Consulted only on a frame
        whose uncut fit already failed to explain the trace.
    The baseline anchor is re-measured on the full spectrum every frame (nothing carried).
    """

    current_phase: Angle | None = None

    def __init__(self, config: StabilizationConfig) -> None:
        self._config = config
        self._ref_policy = ReferencePolicy()
        self._clip_cache = ClipCache()

    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray,
               skipped: int = 0) -> bool:
        """Fit one spectrum. ``skipped`` = frames coalesced away since the last
        fit (drop-stale), reported if a fit throws. Returns True on a fresh commit."""
        wl_full = np.asarray(wavelengths_nm, dtype=float)
        inten_full = np.asarray(intensities, dtype=float)
        # Measure the continuum BEFORE windowing -- this is the whole point of the anchor.
        anchor = baseline_anchor(wl_full, inten_full)
        wl, inten = self._window(wl_full, inten_full)

        lam_ref = self._config.params.lambda_ref.value(Prefix.NANO)
        env_center = self._config.params.env_center
        manual_cut_left = self._config.params.manual_cut_left
        t0 = time.perf_counter()
        try:
            result = analyze_trace(wl, inten, self._config.params.tunables(),
                                   anchor=anchor, ref_policy=self._ref_policy,
                                   lambda_ref_nm=lam_ref, clip_cache=self._clip_cache,
                                   env_center=env_center, manual_cut_left=manual_cut_left)
        except Exception:
            # A fresh fit can still fail on a degenerate frame. There is no seed state to
            # unwind (every fit is cold and independent), so just log and let the next
            # frame try again from scratch.
            ms = (time.perf_counter() - t0) * 1e3
            log.exception("fit ERROR skip=%d %.0fms", skipped, ms)
            return False

        if self._config.accepts(result):
            # Report the phase where the fit says it is SUPPORTED. That is lambda_ref
            # itself on a normal frame; it moves to the core centroid only when a clip
            # leaves lambda_ref unsupportable, and ref_fallback flags it when it does.
            phase_ref = result.phase_at(result.ref_wl)
            self._config.params.commit(result, phase_ref)
            self.current_phase = Angle(phase_ref % _TWO_PI)
            return True

        return False

    def _window(self, wavelengths_nm: np.ndarray,
                intensities: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        wl = np.asarray(wavelengths_nm, dtype=float)
        inten = np.asarray(intensities, dtype=float)
        lo = self._config.wavelength_range.min.value(Prefix.NANO)
        hi = self._config.wavelength_range.max.value(Prefix.NANO)
        mask = (wl >= lo) & (wl <= hi)
        return wl[mask], inten[mask]
