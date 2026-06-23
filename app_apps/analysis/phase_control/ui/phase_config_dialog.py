from __future__ import annotations

from typing import TYPE_CHECKING

from base_core.ipc.worker_handle import WorkerStatus
from base_core.quantities.enums import Prefix
from base_qt.ui.form import AngleSpec, BoolSpec, DirtyForm, FloatSpec, IntSpec, LengthSpec, RangeSpec


if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget
    from app_apps.analysis.phase_control.service import PhaseControlService
    from app_apps.analysis.phase_control.ui.stabilization_control_vm import StabilizationControlVM

_STOPPED = (WorkerStatus.NEW, WorkerStatus.PAUSED)


class PhaseConfigDialog(DirtyForm):
    _specs = {
        "lambda0":             LengthSpec("λ₀",             Prefix.NANO, min=700,  max=1000),
        "delta_lambda_fwhm":   LengthSpec("FWHM bandwidth", Prefix.NANO, min=0.1,  max=50),
        "A":                   FloatSpec("Amplitude",        0.0,  10.0),
        "dphi0":               AngleSpec("Phase φ₀"),
        "delta_z":             FloatSpec("δz [mm]",         -10.0, 10.0),
        "delta_beta":          FloatSpec("δβ [ps²]",        -10.0, 10.0),
        "offset":              FloatSpec("Offset",            0.0,   1.0),
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
    # Subprocess owns these during tracking — grey them when running
    _readonly_when_running = frozenset({
        "lambda0", "delta_lambda_fwhm", "A", "dphi0",
        "delta_z", "delta_beta", "offset", "has_acceleration",
    })

    def __init__(
        self,
        svc: PhaseControlService,
        vm: StabilizationControlVM,
        parent: QWidget,
    ) -> None:
        super().__init__("Phase Tracking Configuration", svc._config, parent)
        self._svc = svc

        # Grey fit params when running; refresh them when subprocess syncs
        self.set_running(vm.worker_state not in _STOPPED)
        vm.worker_state_changed.connect(
            lambda status: self.set_running(status not in _STOPPED)
        )
        vm.config_updated.connect(
            lambda: self.refresh_fields(self._readonly_when_running)
        )

    def on_apply(self) -> None:
        self._svc.set_config()
