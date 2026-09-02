from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from base_qt.ui.panel_view import PanelView

from app_apps.io.control_readout.ui.motion_controls import MotionControls
from app_apps.io.control_readout.rgv.ui.view_model import RgvViewModel

TITLE = "RGV100BL HWP"


class RgvControls(MotionControls):
    """``MotionControls`` plus the stabilization interlock.

    Every move -- relative, absolute or home -- goes through ``_confirm_move``. On confirm
    the loop is stopped FIRST and only then does the move go out; there is no path that
    moves the plate while a controller is still driving it.
    """

    def __init__(self, vm: RgvViewModel, parent: QWidget | None = None) -> None:
        super().__init__(TITLE, vm, parent)
        self._rgv_vm = vm
        # Installed rather than subclassed into the view model, so MotionControls and
        # MotionViewModel stay free of any knowledge of phase control -- the other four
        # devices have no interlock and need none.
        vm.confirm_move = self._confirm_move

    def _confirm_move(self, description: str) -> bool:
        if not self._rgv_vm.stabilization_running:
            return True
        answer = QMessageBox.question(
            self,
            "Stabilization is running",
            f"The phase loop is driving this plate.\n\n"
            f"Stop stabilization and {description}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._rgv_vm.stop_stabilization()
        return True


class RgvView(PanelView):
    def __init__(self, vm: RgvViewModel, parent: QWidget) -> None:
        super().__init__(TITLE, parent, vm=vm)
        self._vm = vm
        self.body_layout.addWidget(RgvControls(vm, self))
