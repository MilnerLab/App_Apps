from __future__ import annotations

from dataclasses import fields
from typing import Any, ClassVar, TYPE_CHECKING

from base_core.ipc.worker_handle import WorkerStatus
from base_core.quantities.enums import Prefix
from base_qt.ui.form import BoolSpec, DirtyForm, FloatSpec, LengthSpec, RangeSpec

from app_apps.analysis.phase_control.subprocess.domain.phase_corrector import (
    GAIN_MAX,
    GAIN_MIN,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    FringeFitParams,
)


if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget
    from app_apps.analysis.phase_control.service import PhaseControlService
    from app_apps.analysis.phase_control.ui.stabilization_control_view_model import StabilizationControlViewModel

# Fields are read-only while RUNNING; editable in NEW (not yet started) and PAUSED
# (temporarily halted — distinct from the post-Stop() NEW state, but still editable).
_EDITABLE_STATES = (WorkerStatus.NEW, WorkerStatus.PAUSED)


class PhaseConfigView(DirtyForm):
    # Derived, not hand-listed: a spec name lives on FringeFitParams if and only if
    # FringeFitParams declares it, and every other spec belongs to StabilizationConfig.
    # Spelling this set out by hand made it a fourth list that had to agree with _specs,
    # _groups and _readonly_when_running, and it silently fell out of step during the v3
    # port -- a field routed to the wrong object only fails at _populate, i.e. on app
    # start. The two dataclasses share no field names, so this stays unambiguous.
    _PARAMS_FIELDS: ClassVar[frozenset[str]] = frozenset(
        f.name for f in fields(FringeFitParams)
    )

    # The folded-chirp knobs (ratio, sigma_init, phase_loss_scale, signal_loss_frac,
    # init_smooth_div) went away with that pipeline: the v3 analysis owns those as
    # harness-calibrated constants, and exposing constants nobody can tune from the
    # instrument only invites mis-setting them.
    _specs = {
        "trunc_threshold":   FloatSpec("Truncation threshold",     0.0,   1.0,    decimals=2, step=0.05),
        "trust_nsig":        FloatSpec("Trust margin (sigmas)",    1.0,   16.0,   decimals=2, step=0.25),
        "lambda_ref":        LengthSpec("λ_ref (preferred)", Prefix.NANO, min=700, max=1000),
        "wavelength_range":  RangeSpec(
            "Wavelength range",
            LengthSpec("", Prefix.NANO, min=700, max=1000),
        ),
        "rms_frac_threshold": FloatSpec("Accept rms/amp below",    0.0,   2.0,     decimals=3, step=0.02),
        "inlier_threshold":  FloatSpec("Accept inliers above (%)", 0.0,   100.0,   decimals=0, step=1.0),
        "min_visibility":    FloatSpec("Abort fit below visibility", 0.0, 1.0,   decimals=3, step=0.01),
        "loop_gain":         FloatSpec("Loop gain (err/frame)", GAIN_MIN, GAIN_MAX, decimals=2, step=0.01),
        "invert_correction": BoolSpec("Invert correction sign"),
    }
    _groups = [
        ("Fit tunables", [
            "trunc_threshold", "trust_nsig", "lambda_ref",
        ]),
        ("Tracking", [
            "wavelength_range", "rms_frac_threshold", "inlier_threshold", "min_visibility",
        ]),
        ("Control loop", [
            "loop_gain", "invert_correction",
        ]),
    ]
    # invert_correction is deliberately NOT read-only while running either, and for a
    # stronger reason than loop_gain: a wrong sign is only diagnosable by watching the loop
    # fail to converge, so the operator must be able to flip it against the running loop.
    # loop_gain is deliberately NOT here: tuning the gain against a loop you are watching
    # settle is the entire reason it is exposed, and it is safe to change mid-run (the
    # corrector is retuned in place, and the fit does not depend on it at all).
    _readonly_when_running = frozenset({
        "trunc_threshold", "trust_nsig", "lambda_ref",
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
