from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from base_core.math.enums import AngleUnit
from base_core.math.models import Angle
from base_qt.ui.form import AngleSpec, ConfigForm
from base_qt.ui.worker_control_widget import WorkerControlWidget

from app_apps.io.control_readout.ell14.ui.view_model import ELL14RotatorViewModel


@dataclass
class _RotateCommand:
    angle: Angle = Angle(0, AngleUnit.DEG, wrap=False)


class ELL14RotatorView(ConfigForm):
    _specs = {
        "angle": AngleSpec("Rotate by"),
    }

    def __init__(self, vm: ELL14RotatorViewModel, parent: QWidget) -> None:
        self._vm = vm
        super().__init__("ELL14 Rotator", _RotateCommand(), parent, vm=vm)

        ctrl = WorkerControlWidget(vm.start, vm.pause, vm.resume, vm.stop, parent=self)
        ctrl.set_status(vm.worker_status)
        vm.worker_state_changed.connect(ctrl.set_status)
        self.header_layout.addWidget(ctrl)
        self.header_widget.setVisible(True)

        home_row = QHBoxLayout()
        home_row.addStretch(1)
        home_btn = QPushButton("Home")
        home_btn.clicked.connect(vm.home)
        home_row.addWidget(home_btn)
        self.body_layout.addLayout(home_row)

    def on_apply(self) -> None:
        self._vm.rotate(self._config.angle)
