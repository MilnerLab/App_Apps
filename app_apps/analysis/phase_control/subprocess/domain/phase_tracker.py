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

# DIAGNOSTIC SWAP (feature branch only — do NOT merge to main):
# The lmfit skew-spectrum fit is replaced by the vendored fringe_fit.analyze_trace
# (see fringe_fit.py). We report phi(lam_ref) as the phase, and gate commits on the
# fit's own quality metric (fringe correlation, ~1 = good) instead of the old
# residuals_threshold. Every spectrum is fitted and the phase updated immediately
# (no avg_spectra batching) so a bad/failed fit is impossible to miss in the logs.
LAM_REF_NM = 802.0          # fixed reference wavelength for the stabilization phase
MIN_FRINGE_CORR = 0.5       # reject fits whose data<->model correlation is below this


class PhaseTracker:
    """
    Tracks the current phase by fitting a fringe model to incoming spectra.

    Accepts raw numpy arrays (wavelengths in nm, intensities) directly — the
    fringe fitter auto-windows to the illuminated band, so the raw trace is
    passed straight through (no clipping/normalisation).

    current_phase is None until the first fit that passes the quality gate.
    """

    current_phase: Angle | None = None

    def __init__(self, start_config: StabilizationConfig) -> None:
        self._config: StabilizationConfig = start_config

    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray) -> bool:
        """Fit one spectrum. Return True if current_phase was updated."""
        w = np.asarray(wavelengths_nm, dtype=float)
        y = np.asarray(intensities, dtype=float)

        # --- run the fit; any exception here means the fit FAILED outright ------
        try:
            res = fringe_fit.analyze_trace(w, y, lam_ref=LAM_REF_NM)
        except Exception:
            log.error(
                "FIT FAILED (exception in analyze_trace) — spectrum could not be fit; "
                "current_phase stays %s. n_points=%d, wl=[%.2f..%.2f] nm",
                self._phase_str(), w.size,
                float(w.min()) if w.size else float("nan"),
                float(w.max()) if w.size else float("nan"),
                exc_info=True,
            )
            return False

        phase_ref = res.get("phase_ref")
        q = res.get("quality", {})
        corr = q.get("fringe_corr", float("nan"))
        vis = res.get("visibility", float("nan"))
        rms = q.get("rms", float("nan"))
        n_peaks = q.get("n_peaks", 0)

        # --- the fit ran but produced no usable phase --------------------------
        if phase_ref is None or not np.isfinite(phase_ref):
            log.error(
                "FIT FAILED (no finite phase: phase_ref=%r, corr=%.3f, vis=%.3f, n_peaks=%d) — "
                "current_phase stays %s",
                phase_ref, corr, vis, n_peaks, self._phase_str(),
            )
            return False

        phase = Angle(float(phase_ref), AngleUnit.RAD)

        # --- the fit ran but the model does not match the data well ------------
        if not np.isfinite(corr) or corr < MIN_FRINGE_CORR:
            log.warning(
                "FIT REJECTED (low quality): fringe_corr=%.3f < %.2f (vis=%.3f, rms=%.2f, "
                "n_peaks=%d). phase would be %.3f deg but current_phase stays %s",
                corr, MIN_FRINGE_CORR, vis, rms, n_peaks, phase.Deg, self._phase_str(),
            )
            return False

        # --- good fit: accept and update the phase -----------------------------
        self.current_phase = phase
        self._config.params.theta0 = phase
        self._config.params.residual = float(rms) if np.isfinite(rms) else 0.0
        log.info(
            "FIT OK: phase=%.3f deg (corr=%.3f, vis=%.3f, rms=%.2f, n_peaks=%d, band=%.1f-%.1f nm)",
            phase.Deg, corr, vis, rms, n_peaks, res["band"][0], res["band"][1],
        )
        return True

    def _phase_str(self) -> str:
        return f"{self.current_phase.Deg:.3f} deg" if self.current_phase is not None else "None"
