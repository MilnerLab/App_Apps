from __future__ import annotations

from PySide6.QtWidgets import QWidget

from base_qt.ui.panel_view import PanelView
from base_qt.ui.worker_control_widget import WorkerControlWidget

from app_apps.io.control_readout.uts150cc.ui.view_model import Uts150ccViewModel


class Uts150ccView(PanelView):
    def __init__(self, vm: Uts150ccViewModel, parent: QWidget) -> None:
        super().__init__("UTS150CC Stage", parent, vm=vm)
        self._vm = vm

        ctrl = WorkerControlWidget(vm.start, vm.pause, vm.reset, parent=self)
        ctrl.set_status(vm.worker_status)
        vm.worker_state_changed.connect(ctrl.set_status)
        self.body_layout.addWidget(ctrl)
