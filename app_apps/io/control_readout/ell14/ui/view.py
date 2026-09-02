from __future__ import annotations

from PySide6.QtWidgets import QWidget

from base_qt.ui.panel_view import PanelView

from app_apps.io.control_readout.ui.motion_controls import MotionControls
from app_apps.io.control_readout.ell14.ui.view_model import ELL14RotatorViewModel

TITLE = "ELL14 Rotator"


class ELL14RotatorView(PanelView):
    def __init__(self, vm: ELL14RotatorViewModel, parent: QWidget) -> None:
        super().__init__(TITLE, parent, vm=vm)
        self._vm = vm
        self.body_layout.addWidget(MotionControls(TITLE, vm, self))
