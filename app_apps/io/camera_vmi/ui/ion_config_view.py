from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING, Any, ClassVar

from base_qt.ui.form import (
    AngleSpec,
    BoolSpec,
    DirtyForm,
    DirtyFormWidget,
    FloatSpec,
    IntSpec,
    RangeSpec,
)

from app_apps.analysis.ion_detection import ContourConfig

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget
    from app_apps.io.camera_vmi.ui.ion_view_model import IonViewModel


class IonConfigForm(DirtyFormWidget):
    """Edits IonSettings and its ContourConfig in place -- see ``_obj``."""

    # Derived, not hand-listed, as in PhaseConfigForm: a field is routed to the
    # ContourConfig if and only if ContourConfig declares it.
    _CONTOUR_FIELDS: ClassVar[frozenset[str]] = frozenset(f.name for f in fields(ContourConfig))

    _specs = {
        "adaptive":            BoolSpec("Adaptive threshold"),
        "threshold_value":     IntSpec("Threshold (fixed)", 0, 255),
        "adaptive_blocksize":  IntSpec("Adaptive block size", 3, 255, step=2),
        "adaptive_shift":      FloatSpec("Adaptive shift", -255.0, 255.0, decimals=1, step=0.5),
        "contour_min_size":    IntSpec("Min contour points", 1, 1000),
        "contour_min_area":    IntSpec("Min area (px²)", 0, 100_000),
        "contour_max_area":    IntSpec("Max area (px²)", 0, 100_000),
        "center_x":            FloatSpec("Center x (px)", 0.0, 10_000.0, decimals=1, step=1.0),
        "center_y":            FloatSpec("Center y (px)", 0.0, 10_000.0, decimals=1, step=1.0),
        "angle":               AngleSpec("Rotation"),
        "transform_parameter": FloatSpec("x scale (transform)", 0.01, 100.0, decimals=3, step=0.01),
        "analysis_zone":       RangeSpec(
            "Analysis zone (px)",
            FloatSpec("", 0.0, 10_000.0, decimals=1, step=1.0),
        ),
        "rolling_frames":      IntSpec("Rolling window (frames)", 1, 100_000),
    }
    _groups = [
        ("Detection", [
            "adaptive", "threshold_value", "adaptive_blocksize", "adaptive_shift",
            "contour_min_size", "contour_min_area", "contour_max_area",
        ]),
        ("Transform", [
            "center_x", "center_y", "angle", "transform_parameter", "analysis_zone",
        ]),
        ("Display", [
            "rolling_frames",
        ]),
    ]

    def __init__(self, vm: IonViewModel, parent: QWidget | None = None) -> None:
        # Both set before super().__init__ builds and populates the fields.
        self._vm = vm
        self._contour = vm.settings.contour
        super().__init__(vm.settings, parent)

    def _obj(self, name: str) -> Any:
        return self._contour if name in self._CONTOUR_FIELDS else self._config

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
        self._vm.apply_config()


class IonConfigView(DirtyForm):
    """The popout wrapper. Built inline by ``IonView`` with its VM, so no ``vm=``."""

    def __init__(self, vm: IonViewModel, parent: QWidget) -> None:
        self._vm = vm
        super().__init__("Ion Detection Configuration", parent)
        vm.config_updated.connect(self.form.reload)

    def build_form(self) -> IonConfigForm:
        return IonConfigForm(self._vm, self)
