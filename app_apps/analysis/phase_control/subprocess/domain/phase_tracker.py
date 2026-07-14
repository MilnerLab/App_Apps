from __future__ import annotations

import logging

import numpy as np

from base_core.math.enums import AngleUnit
from base_core.math.models import Angle
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    StabilizationConfig,
)
from app_apps.analysis.phase_control.subprocess.domain import fringe_fit

log = logging.getLogger(__name__)

# The stabilization phase is phi(lam_ref) mod 2pi from fringe_fit (see fringe_fit.py).
# Every spectrum is fitted and the phase updated immediately (no avg_spectra batching),
# so a bad/failed fit is impossible to miss in the logs.
LAM_REF_NM = 802.0          # fixed reference wavelength for the stabilization phase
MIN_FRINGE_CORR = 0.5       # reject fits whose data<->model correlation is below this
REDO_AFTER_BAD = 10         # after this many consecutive degraded fits, re-seed cold


class PhaseTracker:
    """
    Tracks the current phase by fitting a fringe model to incoming spectra.

    Two-path fit (see fringe_fit.analyze_trace):
      * COLD guess — the expensive envelope fits + sliding-window FFT frequency
        search — runs once on the first spectrum after a (re)start, and again to
        re-seed whenever the fit degrades for REDO_AFTER_BAD spectra in a row.
      * WARM re-fit — envelopes + full cubic phase warm-started from the previous
        good fit in a fixed lam0 frame, skipping the FFT search — runs every other
        spectrum. Fast and robust, and keeps phase_ref comparable across traces.

    Accepts raw numpy arrays (wavelengths in nm, intensities) directly — the
    fringe fitter auto-windows to the illuminated band.

    current_phase is None until the first fit that passes the quality gate.
    """

    current_phase: Angle | None = None

    def __init__(self, start_config: StabilizationConfig) -> None:
        self._config: StabilizationConfig = start_config
        self._seed: dict | None = None    # "_warm" payload of the last GOOD fit (None => cold)
        self._bad_streak: int = 0         # consecutive degraded fits since the last good one
        self.last_result: dict | None = None  # last GOOD result dict (for the display overlay)

    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray) -> bool:
        """Fit one spectrum. Return True if current_phase was updated."""
        w = np.asarray(wavelengths_nm, dtype=float)
        y = np.asarray(intensities, dtype=float)
        warm = self._seed is not None
        mode = "WARM" if warm else "COLD"

        # --- run the fit; any exception here means the fit FAILED outright ------
        try:
            res = fringe_fit.analyze_trace(w, y, lam_ref=LAM_REF_NM, seed=self._seed)
        except Exception:
            log.error(
                "FIT FAILED (%s: exception in analyze_trace) — spectrum could not be fit; "
                "current_phase stays %s. n_points=%d, wl=[%.2f..%.2f] nm",
                mode, self._phase_str(), w.size,
                float(w.min()) if w.size else float("nan"),
                float(w.max()) if w.size else float("nan"),
                exc_info=True,
            )
            return self._register_bad()

        phase_ref = res.get("phase_ref")
        q = res.get("quality", {})
        corr = q.get("fringe_corr", float("nan"))
        vis = res.get("visibility", float("nan"))
        rms = q.get("rms", float("nan"))
        n_peaks = q.get("n_peaks", 0)

        # --- the fit ran but produced no usable phase --------------------------
        if phase_ref is None or not np.isfinite(phase_ref):
            log.error(
                "FIT FAILED (%s: no finite phase: phase_ref=%r, corr=%.3f, vis=%.3f) — "
                "current_phase stays %s",
                mode, phase_ref, corr, vis, self._phase_str(),
            )
            return self._register_bad()

        phase = Angle(float(phase_ref), AngleUnit.RAD)

        # --- the fit ran but the model does not match the data well ------------
        if not np.isfinite(corr) or corr < MIN_FRINGE_CORR:
            log.warning(
                "FIT REJECTED (%s low quality): fringe_corr=%.3f < %.2f (vis=%.3f, rms=%.2f). "
                "phase would be %.3f deg but current_phase stays %s",
                mode, corr, MIN_FRINGE_CORR, vis, rms, phase.Deg, self._phase_str(),
            )
            return self._register_bad()

        # --- good fit: accept, update the phase, warm-start the next spectrum ---
        self.current_phase = phase
        self._config.params.theta0 = phase
        self._config.params.residual = float(rms) if np.isfinite(rms) else 0.0
        self._seed = res["_warm"]
        self._bad_streak = 0
        self.last_result = res
        log.info(
            "FIT OK (%s): phase=%.3f deg (corr=%.3f, vis=%.3f, rms=%.2f, n_peaks=%d, "
            "band=%.1f-%.1f nm)",
            mode, phase.Deg, corr, vis, rms, n_peaks, res["band"][0], res["band"][1],
        )
        return True

    def _register_bad(self) -> bool:
        """Record a degraded/failed fit; drop the warm seed once the streak is too long."""
        self._bad_streak += 1
        if self._seed is not None and self._bad_streak >= REDO_AFTER_BAD:
            log.warning(
                "PhaseTracker: %d consecutive degraded fits — dropping warm seed, "
                "next spectrum re-seeds COLD (envelope fits + FFT).",
                self._bad_streak,
            )
            self._seed = None
        return False

    def _phase_str(self) -> str:
        return f"{self.current_phase.Deg:.3f} deg" if self.current_phase is not None else "None"
