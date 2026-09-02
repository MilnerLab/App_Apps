from __future__ import annotations

from PySide6.QtWidgets import QWidget

from base_qt.ui.panel_view import PanelView

from app_apps.io.control_readout.ui.motion_controls import MotionControls
from app_apps.io.control_readout.mfa_cc.ui.view_model import MfaccViewModel


class MfaccView(PanelView):
    """The floating Devices-menu popout. The panel embeds the same ``MotionControls``
    block directly, so there is one implementation of the controls, not two."""

    def __init__(self, vm: MfaccViewModel, parent: QWidget) -> None:
        super().__init__("MFA-CC Stage (delay)", parent, vm=vm)
        self._vm = vm
        self.body_layout.addWidget(MotionControls("MFA-CC Stage (delay)", vm, self))
