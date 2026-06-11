from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnalysisResult:
    """Published after each analysis pass; VM displays these fit parameters."""
    central_wavelength_nm: float
    bandwidth_nm: float
    envelope_peak: float
    fit_residual: float


@dataclass(frozen=True)
class CalibrationApplied:
    """Published when the calibration step has sent a correction to hardware."""
    correction_deg: float
