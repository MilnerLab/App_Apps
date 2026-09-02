from __future__ import annotations

import logging
import math
import time

import numpy as np

from base_core.math.models import Angle
from base_core.quantities.enums import Prefix
from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import (
    ReferencePolicy,
    analyze_trace,
    baseline_anchor,
)
from app_apps.analysis.phase_control.subprocess.domain.fringe_visibility import (
    fringe_visibility,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    StabilizationConfig,
)

log = logging.getLogger(__name__)

_TWO_PI = 2.0 * math.pi

# Seconds between "holding" lines while visibility stays low. Without this the hold logs at
# the frame rate for as long as the operator is changing settings, which is exactly when the
# log has to stay readable.
_HOLD_LOG_PERIOD_S = 2.0


class PhaseTracker:
    """Per-shot fringe-phase tracker.

    Each spectrum is windowed and fit fresh by ``fringe_fit.analyze_trace`` -- a cold,
    seed-independent fit on every shot (NO warm-starting). A fit that passes
    ``config.accepts`` commits its outputs into ``config.params`` (for the overlay) and
    sets ``current_phase`` = cubic phase at ``lambda_ref`` mod 2pi.

    ``current_phase`` is None until the first accepted fit, then holds the last
    committed value. ``update`` returns True only when a fresh fit committed.

    Almost nothing is stateful across frames, and the exceptions are these:
      * the baseline anchor is measured on the FULL spectrum before windowing (the analysis
        window holds no continuum at all, so the envelope offset has nothing to pin it and
        the quantile loss floats it upward -- offset 255 against a truth of 155 on real
        bright data). It is re-measured every frame; nothing is carried.
      * ``_last_hold_log`` rate-limits the low-visibility hold message. Diagnostics only.
      * ``_ref_policy`` carries the phase-reference hysteresis, the only state that can
        change a fit's OUTPUT. It exists so a stabilization loop locked to one wavelength
        cannot chatter between two when a clip sits near the core.
    The FIT itself remains cold and seed-independent on every shot.
    """

    current_phase: Angle | None = None

    def __init__(self, config: StabilizationConfig) -> None:
        self._config = config
        self._ref_policy = ReferencePolicy()
        self._last_hold_log = 0.0

    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray,
               skipped: int = 0) -> bool:
        """Fit one spectrum. ``skipped`` = frames coalesced away since the last
        fit (drop-stale), reported in the log line. Returns True on a fresh commit."""
        wl_full = np.asarray(wavelengths_nm, dtype=float)
        inten_full = np.asarray(intensities, dtype=float)
        # Measure the continuum BEFORE windowing -- this is the whole point of the anchor.
        anchor = baseline_anchor(wl_full, inten_full)
        wl, inten = self._window(wl_full, inten_full)

        # Contrast gate, BEFORE the optimizer. A trace whose fringes have washed out (settings
        # changing, the beam moving) costs up to 47 s in the cold fit and then reports
        # status="ok" on a phase fit to noise -- a confident number with no physical basis,
        # handed straight to the control loop. ~1.9 ms here buys that back; see
        # fringe_visibility. On abort: no fit, no commit, no correction. The spectrometer
        # stream is untouched and the loop simply holds until the fringes come back.
        vis = fringe_visibility(inten)
        if vis < self._config.min_visibility:
            now = time.perf_counter()
            if now - self._last_hold_log >= _HOLD_LOG_PERIOD_S:
                self._last_hold_log = now
                log.info("holding: visibility %.3f < %.3f (no fit, no correction) skip=%d",
                         vis, self._config.min_visibility, skipped)
            return False

        lam_ref = self._config.params.lambda_ref.value(Prefix.NANO)
        t0 = time.perf_counter()
        try:
            result = analyze_trace(wl, inten, self._config.params.tunables(),
                                   anchor=anchor, ref_policy=self._ref_policy,
                                   lambda_ref_nm=lam_ref)
        except Exception:
            # A fresh fit can still fail on a degenerate frame. There is no seed state to
            # unwind (every fit is cold and independent), so just log and let the next
            # frame try again from scratch.
            ms = (time.perf_counter() - t0) * 1e3
            log.exception("fit ERROR skip=%d %.0fms", skipped, ms)
            return False
        ms = (time.perf_counter() - t0) * 1e3

        if self._config.accepts(result):
            # Report the phase where the fit says it is SUPPORTED. That is lambda_ref
            # itself on a normal frame; it moves to the core centroid only when a clip
            # leaves lambda_ref unsupportable, and ref_fallback flags it when it does.
            phase_ref = result.phase_at(result.ref_wl)
            self._config.params.commit(result, phase_ref)
            self.current_phase = Angle(phase_ref % _TWO_PI)
            log.info("fit ok  phi=%.3frad @%.2fnm%s rms=%.0f inl=%.0f%% skip=%d %.0fms",
                     phase_ref % _TWO_PI, result.ref_wl,
                     " (REF MOVED)" if result.ref_fallback else "",
                     result.rms_sig, result.inlier_pct, skipped, ms)
            return True

        rms = "inf" if not np.isfinite(result.rms_sig) else f"{result.rms_sig:.0f}"
        log.info("fit REJECT [%s] rms=%s inl=%.0f%% trust=%s skip=%d %.0fms  %s",
                 result.status, rms, result.inlier_pct, result.trust_ok, skipped, ms,
                 result.msg)
        return False

    def _window(self, wavelengths_nm: np.ndarray,
                intensities: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        wl = np.asarray(wavelengths_nm, dtype=float)
        inten = np.asarray(intensities, dtype=float)
        lo = self._config.wavelength_range.min.value(Prefix.NANO)
        hi = self._config.wavelength_range.max.value(Prefix.NANO)
        mask = (wl >= lo) & (wl <= hi)
        return wl[mask], inten[mask]
