from __future__ import annotations

from PySide6.QtWidgets import QWidget

from base_qt.ui.panel_view import PanelView

from app_apps.io.control_readout.ui.motion_controls import MotionControls
from app_apps.io.control_readout.fms300pp.ui.view_model import Fms300ppViewModel

TITLE = "FMS300PP Stage (probe)"


class Fms300ppView(PanelView):
    """The floating Devices-menu popout. The panel embeds the same ``MotionControls``
    block directly, so there is one implementation of the controls, not two.

    No ``vm=``: the view model is a singleton shared with the Devices page, so closing
    this popout must hide it rather than tear those subscriptions down."""

    def __init__(self, vm: Fms300ppViewModel, parent: QWidget) -> None:
        super().__init__(TITLE, parent)
        self._vm = vm
        self.add_worker_controls(vm)
        self.body_layout.addWidget(MotionControls(vm, self))
