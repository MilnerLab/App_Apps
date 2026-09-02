from __future__ import annotations

from PySide6.QtWidgets import QWidget

from base_qt.ui.panel_view import PanelView

from app_apps.io.control_readout.ui.motion_controls import MotionControls
from app_apps.io.control_readout.uts150cc.ui.view_model import Uts150ccViewModel


class Uts150ccView(PanelView):
    """The floating Devices-menu popout. The panel embeds the same ``MotionControls``
    block directly, so there is one implementation of the controls, not two."""

    def __init__(self, vm: Uts150ccViewModel, parent: QWidget) -> None:
        super().__init__("UTS150CC Stage (grating)", parent, vm=vm)
        self._vm = vm
        self.body_layout.addWidget(MotionControls("UTS150CC Stage (grating)", vm, self))
