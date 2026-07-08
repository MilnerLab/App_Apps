from __future__ import annotations

from PySide6.QtWidgets import QWidget

from base_qt.ui.panel_view import PanelView
from base_qt.ui.worker_control_widget import WorkerControlWidget

from app_apps.io.control_readout.mfa_cc.ui.view_model import MfaccViewModel


class MfaccView(PanelView):
    def __init__(self, vm: MfaccViewModel, parent: QWidget) -> None:
        super().__init__("MFA-CC Stage", parent, vm=vm)
        self._vm = vm

        ctrl = WorkerControlWidget(vm.start, vm.pause, vm.resume, vm.stop, parent=self)
        ctrl.set_status(vm.worker_status)
        vm.worker_state_changed.connect(ctrl.set_status)
        self.header_layout.addWidget(ctrl)
        self.header_widget.setVisible(True)
