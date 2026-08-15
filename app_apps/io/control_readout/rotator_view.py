from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from base_core.math.enums import AngleUnit
from base_core.math.models import Angle
from base_qt.ui.form import AngleSpec, ConfigForm
from base_qt.ui.worker_control_widget import WorkerControlWidget

from app_apps.io.control_readout.rotator_view_model import RotatorViewModel


@dataclass
class _RotateCommand:
    angle: Angle = Angle(0, AngleUnit.DEG, wrap=False)


class RotatorView(ConfigForm):
    """Reusable panel for any motorized-waveplate rotator: editable
    "Rotate by" angle with a live current-angle readout, a Home button, and
    Start/Pause worker controls.

    One instance per device (ELL14, RGV100BL, ...) — parameterized by title
    and ViewModel rather than subclassed.
    """

    _specs = {
        "angle": AngleSpec("Rotate by"),
    }

    def __init__(self, title: str, vm: RotatorViewModel, parent: QWidget) -> None:
        self._vm = vm
        super().__init__(title, _RotateCommand(), parent, vm=vm)

        ctrl = WorkerControlWidget(vm.start, vm.pause, vm.resume, vm.stop, parent=self)
        ctrl.set_status(vm.worker_status)
        vm.worker_state_changed.connect(ctrl.set_status)
        self.header_layout.addWidget(ctrl)
        self.header_widget.setVisible(True)

        vm.angle_updated.connect(lambda deg: self.update_readout("angle", Angle(deg, AngleUnit.DEG)))

        home_row = QHBoxLayout()
        home_row.addStretch(1)
        home_btn = QPushButton("Home")
        home_btn.clicked.connect(vm.home)
        home_row.addWidget(home_btn)
        self.body_layout.addLayout(home_row)

    def on_apply(self) -> None:
        self._vm.rotate(self._config.angle)
