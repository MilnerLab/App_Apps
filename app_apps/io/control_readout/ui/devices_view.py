"""The Devices panel: every motion device on one dockable page.

Previously these were seven separate floating popouts off the Devices menu, and four of
them had nothing on them but Start/Pause/Resume/Stop -- no readout, no way to move anything.
An alignment session means moving between the delay stage, the grating and the mirrors
repeatedly, and a popout you have to open, drag, read and close for each of those is a
worse tool than one page you leave open.

Ordering is by how often the operator touches them during alignment: mirrors first (the
whole reason the picomotor block exists), then the two stages that move the interferogram,
then the probe, then the two rotators. The RGV is last on purpose: it is the one device
with an interlock, and it is not one you reach for casually.

The menu popouts still work and are built from the same widgets -- see ``MotionControls``
and ``PicomotorControls``.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from base_core.framework.di import Container

from app_apps.io.control_readout.ell14.ui.view_model import ELL14RotatorViewModel
from app_apps.io.control_readout.fms300pp.ui.view_model import Fms300ppViewModel
from app_apps.io.control_readout.mfa_cc.ui.view_model import MfaccViewModel
from app_apps.io.control_readout.picomotor.ui.view import PicomotorControls
from app_apps.io.control_readout.picomotor.ui.view_model import PicomotorViewModel
from app_apps.io.control_readout.rgv.ui.view import RgvControls
from app_apps.io.control_readout.rgv.ui.view_model import RgvViewModel
from app_apps.io.control_readout.ui.motion_controls import MotionControls
from app_apps.io.control_readout.uts150cc.ui.view_model import Uts150ccViewModel

#: (view-model type, block title), in the order they appear. See the module docstring.
_STAGES = [
    (MfaccViewModel, "MFA-CC — centrifuge delay"),
    (Uts150ccViewModel, "UTS150CC — grating"),
    (Fms300ppViewModel, "FMS300PP — probe"),
    (ELL14RotatorViewModel, "ELL14 rotator"),
]


class DevicesView(QWidget):
    def __init__(self, container: Container, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        body = QWidget()
        stack = QVBoxLayout(body)
        stack.setContentsMargins(8, 8, 8, 8)
        stack.setSpacing(10)

        mirrors = QGroupBox("Mirror picomotors (8742)")
        mirrors_lay = QVBoxLayout(mirrors)
        mirrors_lay.addWidget(PicomotorControls(container.get(PicomotorViewModel), mirrors))
        stack.addWidget(mirrors)

        for vm_type, title in _STAGES:
            stack.addWidget(MotionControls(title, container.get(vm_type), body))

        # Last, and with its own class: moving this plate by hand while the phase loop is
        # driving it is two controllers fighting over one optic, so every move here is
        # gated by a confirmation that stops the loop first.
        stack.addWidget(RgvControls(container.get(RgvViewModel), body))
        stack.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
