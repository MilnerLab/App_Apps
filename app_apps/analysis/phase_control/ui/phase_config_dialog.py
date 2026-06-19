from __future__ import annotations

from PySide6.QtWidgets import QWidget

from app_apps.analysis.phase_control.service import PhaseControlService
from base_core.quantities.enums import Prefix
from base_qt.ui.form import (
    AngleSpec,
    BoolSpec,
    ConfigForm,
    FloatSpec,
    IntSpec,
    LengthSpec,
    RangeSpec,
)


class PhaseConfigDialog(ConfigForm):
    _specs = {
        "lambda0":             LengthSpec("λ₀",              Prefix.NANO, min=700,  max=1000),
        "delta_lambda_fwhm":   LengthSpec("FWHM bandwidth",  Prefix.NANO, min=0.1,  max=50),
        "A":                   FloatSpec("Amplitude",         0.0,  10.0),
        "dphi0":               AngleSpec("Phase φ₀"),
        "delta_z":             FloatSpec("δz [mm]",          -10.0, 10.0),
        "delta_beta":          FloatSpec("δβ [ps²]",          -10.0, 10.0),
        "offset":              FloatSpec("Offset",             0.0,   1.0),
        "has_acceleration":    BoolSpec("Asymmetric chirp (use cfg_spectrum)"),
        "wavelength_range":    RangeSpec(
            "Wavelength range",
            LengthSpec("", Prefix.NANO, min=700, max=1000),
        ),
        "residuals_threshold": FloatSpec("Residuals threshold", 0.0, 1000.0, decimals=1, step=1.0),
        "avg_spectra":         IntSpec("Averaging window", 1, 100),
    }
    _groups = [
        ("Spectral Fit", [
            "lambda0", "delta_lambda_fwhm", "A", "dphi0",
            "delta_z", "delta_beta", "offset", "has_acceleration",
        ]),
        ("Tracking", [
            "wavelength_range", "residuals_threshold", "avg_spectra",
        ]),
    ]

    def __init__(self, svc: PhaseControlService, parent: QWidget) -> None:
        super().__init__("Phase Tracking Configuration", svc._config, parent)
        self._svc = svc

    def on_apply(self) -> None:
        self._svc.set_config()
