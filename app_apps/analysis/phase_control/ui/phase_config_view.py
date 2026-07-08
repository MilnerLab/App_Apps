from __future__ import annotations

from typing import Any, ClassVar, TYPE_CHECKING

from base_core.ipc.worker_handle import WorkerStatus
from base_core.quantities.enums import Prefix
from base_qt.ui.form import AngleSpec, BoolSpec, DirtyForm, FloatSpec, GDDSpec, IntSpec, LengthSpec, RangeSpec, TimeSpec


if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget
    from app_apps.analysis.phase_control.service import PhaseControlService
    from app_apps.analysis.phase_control.ui.stabilization_control_view_model import StabilizationControlViewModel

# Fields are read-only while RUNNING; editable in NEW (not yet started) and PAUSED
# (temporarily halted — distinct from the post-Stop() NEW state, but still editable).
_EDITABLE_STATES = (WorkerStatus.NEW, WorkerStatus.PAUSED)


class PhaseConfigView(DirtyForm):
    _PARAMS_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "lambda0", "delta_lambda_fwhm", "R", "L",
        "theta0", "theta1", "theta2",
        "alpha_R", "epsilon_R", "s_R", "alpha_L", "epsilon_L", "s_L", "offset",
    })

    _specs = {
        "lambda0":             LengthSpec("λ₀",             Prefix.NANO, min=700,  max=1000),
        "delta_lambda_fwhm":   LengthSpec("FWHM bandwidth", Prefix.NANO, min=0.1,  max=50),
        "R":                   FloatSpec("Amplitude R (reference arm)", 0.0, 10.0),
        "L":                   FloatSpec("Amplitude L (grating arm)",   0.0, 10.0),
        "theta0":              AngleSpec("Phase θ₀"),
        "theta1":              TimeSpec("θ₁", Prefix.PICO, min=-10.0, max=10.0),
        "theta2":              GDDSpec("θ₂", Prefix.PICO, min=-10.0, max=10.0),
        "alpha_R":             FloatSpec("Skewness α (R)",      -50.0,   50.0,   decimals=3, step=0.1),
        "epsilon_R":           FloatSpec("Skew location ε (R)", -100.0,  100.0,  decimals=3, step=0.1),
        "s_R":                 FloatSpec("Skew scale s (R)",      0.0001, 200.0, decimals=3, step=0.1),
        "alpha_L":             FloatSpec("Skewness α (L)",      -50.0,   50.0,   decimals=3, step=0.1),
        "epsilon_L":           FloatSpec("Skew location ε (L)", -100.0,  100.0,  decimals=3, step=0.1),
        "s_L":                 FloatSpec("Skew scale s (L)",      0.0001, 200.0, decimals=3, step=0.1),
        "offset":              FloatSpec("Offset",            0.0,   float("inf")),
        "wavelength_range":    RangeSpec(
            "Wavelength range",
            LengthSpec("", Prefix.NANO, min=700, max=1000),
        ),
        "residuals_threshold": FloatSpec("Residuals threshold", 0.0, 1000.0, decimals=1, step=1.0),
        "avg_spectra":         IntSpec("Averaging window", 1, 100),
    }
    _groups = [
        ("Spectral Fit", [
            "lambda0", "delta_lambda_fwhm", "R", "L",
            "theta0", "theta1", "theta2",
            "alpha_R", "epsilon_R", "s_R", "alpha_L", "epsilon_L", "s_L", "offset",
        ]),
        ("Tracking", [
            "wavelength_range", "residuals_threshold", "avg_spectra",
        ]),
    ]
    _readonly_when_running = frozenset({
        "lambda0", "delta_lambda_fwhm", "R", "L",
        "theta0", "theta1", "theta2",
        "alpha_R", "epsilon_R", "s_R", "alpha_L", "epsilon_L", "s_L", "offset",
    })

    def __init__(
        self,
        svc: PhaseControlService,
        vm: StabilizationControlViewModel,
        parent: QWidget,
    ) -> None:
        self._params = svc._config.params   # set before super().__init__ calls _populate
        super().__init__("Phase Tracking Configuration", svc._config, parent)
        self._svc = svc

        self.set_running(vm.worker_state not in _EDITABLE_STATES)
        vm.worker_state_changed.connect(
            lambda status: self.set_running(status not in _EDITABLE_STATES)
        )
        vm.config_updated.connect(
            lambda: self.refresh_fields(self._readonly_when_running)
        )

    def _obj(self, name: str) -> Any:
        return self._params if name in self._PARAMS_FIELDS else self._config

    def _populate(self) -> None:
        for name, spec in self._specs.items():
            spec.set_value(self._widgets[name], getattr(self._obj(name), name))
        for ind in self._indicators.values():
            ind.set_dirty(False)

    def _apply(self) -> None:
        for name, spec in self._specs.items():
            setattr(self._obj(name), name, spec.get_value(self._widgets[name]))
        for ind in self._indicators.values():
            ind.set_dirty(False)
        self.on_apply()

    def refresh_fields(self, names: frozenset[str]) -> None:
        for name in names:
            if name not in self._widgets:
                continue
            self._specs[name].set_value(self._widgets[name], getattr(self._obj(name), name))
            self._indicators[name].set_dirty(False)

    def on_apply(self) -> None:
        self._svc.set_config()
