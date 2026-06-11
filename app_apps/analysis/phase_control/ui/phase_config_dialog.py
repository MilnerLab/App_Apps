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
    TimeSpec,
)


class PhaseConfigDialog(ConfigForm):
    _specs = {
        "central_wavelength": LengthSpec("Central wavelength", Prefix.NANO, min=700, max=1000),
        "bandwidth":          LengthSpec("Bandwidth",          Prefix.NANO, min=0.1,  max=50),
        "baseline":           FloatSpec("Baseline",         -10.0, 10.0),
        "phase":              AngleSpec("Phase"),
        "tau_ps":             TimeSpec("τ", Prefix.PICO),
        "a_R_THz_per_ps":     FloatSpec("a_R (THz/ps)",    -10.0, 10.0),
        "a_L_THz_per_ps":     FloatSpec("a_L (THz/ps)",    -10.0, 10.0),
        "has_acceleration":   BoolSpec("Asymmetric chirp"),
        "wavelength_range":   RangeSpec(
            "Wavelength range",
            LengthSpec("", Prefix.NANO, min=700, max=1000),
        ),
        "residuals_threshold": FloatSpec("Residuals threshold", 0.0, 1000.0, decimals=1, step=1.0),
        "avg_spectra":         IntSpec("Averaging window", 1, 100),
    }
    _groups = [
        ("Spectral Fit", [
            "central_wavelength", "bandwidth", "baseline", "phase",
            "tau_ps", "a_R_THz_per_ps", "a_L_THz_per_ps", "has_acceleration",
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
