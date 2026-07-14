from __future__ import annotations

from typing import Any, ClassVar, TYPE_CHECKING

from base_core.ipc.worker_handle import WorkerStatus
from base_core.quantities.enums import Prefix
from base_qt.ui.form import DirtyForm, FloatSpec, IntSpec, LengthSpec, RangeSpec


if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget
    from app_apps.analysis.phase_control.service import PhaseControlService
    from app_apps.analysis.phase_control.ui.stabilization_control_view_model import StabilizationControlViewModel

# Fields are read-only while RUNNING; editable in NEW (not yet started) and PAUSED
# (temporarily halted — distinct from the post-Stop() NEW state, but still editable).
_EDITABLE_STATES = (WorkerStatus.NEW, WorkerStatus.PAUSED)


class PhaseConfigView(DirtyForm):
    _PARAMS_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "ratio", "sigma_init", "trunc_threshold", "phase_loss_scale",
        "signal_loss_frac", "init_smooth_div", "lambda_ref",
    })

    _specs = {
        "ratio":             FloatSpec("Envelope pinball ratio",   1.0,   100.0,  decimals=1, step=1.0),
        "sigma_init":        FloatSpec("Init σ guess (nm)",        0.1,   50.0,   decimals=2, step=0.5),
        "trunc_threshold":   FloatSpec("Truncation threshold",    0.0,   1.0,    decimals=2, step=0.05),
        "phase_loss_scale":  FloatSpec("Phase loss scale (rad)",  0.01,  100.0,  decimals=2, step=0.1),
        "signal_loss_frac":  FloatSpec("Signal loss fraction",    0.01,  10.0,   decimals=2, step=0.1),
        "init_smooth_div":   IntSpec("Null-init smoothing div",   2,     500),
        "lambda_ref":        LengthSpec("λ_ref", Prefix.NANO, min=700, max=1000),
        "wavelength_range":  RangeSpec(
            "Wavelength range",
            LengthSpec("", Prefix.NANO, min=700, max=1000),
        ),
        "rms_threshold":     FloatSpec("Accept RMS below (cts)",  0.0,   10000.0, decimals=1, step=1.0),
        "inlier_threshold":  FloatSpec("Accept inliers above (%)", 0.0,  100.0,   decimals=0, step=1.0),
        "redo_after_bad":    IntSpec("Force cold after N bad",    1,     1000),
    }
    _groups = [
        ("Fit tunables", [
            "ratio", "sigma_init", "trunc_threshold", "phase_loss_scale",
            "signal_loss_frac", "init_smooth_div", "lambda_ref",
        ]),
        ("Tracking", [
            "wavelength_range", "rms_threshold", "inlier_threshold", "redo_after_bad",
        ]),
    ]
    _readonly_when_running = frozenset({
        "ratio", "sigma_init", "trunc_threshold", "phase_loss_scale",
        "signal_loss_frac", "init_smooth_div", "lambda_ref",
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
