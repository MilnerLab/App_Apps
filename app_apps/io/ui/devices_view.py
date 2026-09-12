"""The Devices panel: every device in the rig, on one dockable page, grouped by kind.

Previously these were separate floating popouts off the Devices menu, and four of them had
nothing on them but Start/Pause/Resume/Stop -- no readout, no way to move anything. An
alignment session means moving between the delay stage, the grating and the mirrors
repeatedly, and a popout you have to open, drag, read and close for each of those is a
worse tool than one page you leave open.

**Grouped rather than one flat stack.** With the two acquisition instruments on here as
well, a single column ran long enough that finding a device meant scrolling past five you
did not want. The tab bar is PINNED above the scroll area, so switching groups is always
one click regardless of how far down the current group is scrolled.

Ordering inside each group is by how often the operator touches them during alignment:
mirrors first (the whole reason the picomotor block exists), then the two stages that move
the interferogram, then the probe. The RGV is last in its group on purpose: it is the one
device with an interlock, and it is not one you reach for casually.

Every block here is the SAME widget the matching Devices-menu popout uses -- see
``MotionControls``, ``PicomotorControls``, ``SpectrometerControls`` and
``OscilloscopeControls`` -- arranged by the same ``DeviceFrame``, and driven by the same
view-model instance: the device VMs are container singletons, so a value edited here shows
up in the popout and the other way round.

The blocks are built with ``scrollable=False``. Each one states its true height and the
group page around it owns the single scrollbar; a block that scrolled internally would put
a scrollbar inside a scrollbar.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QScrollArea,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from base_core.framework.di import Container
from base_qt.ui.device_frame import DeviceFrame
from base_qt.ui.form import DirtyFormWidget

from app_apps.io.control_readout.ell14.ui.view_model import ELL14RotatorViewModel
from app_apps.io.control_readout.fms300pp.ui.view_model import Fms300ppViewModel
from app_apps.io.control_readout.mfa_cc.ui.view_model import MfaccViewModel
from app_apps.io.control_readout.picomotor.ui.view import PicomotorControls
from app_apps.io.control_readout.picomotor.ui.view_model import PicomotorViewModel
from app_apps.io.control_readout.rgv.ui.view import RgvControls
from app_apps.io.control_readout.rgv.ui.view_model import RgvViewModel
from app_apps.io.control_readout.ui.motion_controls import MotionControls
from app_apps.io.control_readout.uts150cc.ui.view_model import Uts150ccViewModel
from app_apps.io.oscilloscope.ui.oscilloscope_view import OscilloscopeControls
from app_apps.io.oscilloscope.ui.oscilloscope_view_model import OscilloscopeViewModel
from app_apps.io.spectrometer.ui.spectrometer_view import SpectrometerControls
from app_apps.io.spectrometer.ui.spectrometer_view_model import SpectrometerViewModel

#: (tab label, [block builders]) in tab order. Each builder takes the container and the
#: page widget and returns the block. Adding a device is one entry here and nothing else.
_GROUPS: list[tuple[str, list[Callable[[Container, QWidget], QWidget]]]] = [
    ("Acquisition", [
        lambda c, p: _form_block("Spectrometer", c.get(SpectrometerViewModel),
                                 SpectrometerControls, p),
        lambda c, p: _form_block("Oscilloscope", c.get(OscilloscopeViewModel),
                                 OscilloscopeControls, p),
    ]),
    # "&&" not "&": QTabBar reads a single ampersand as a keyboard mnemonic and eats it,
    # rendering this as "Motors Rotators" with the R underlined.
    ("Motors && Rotators", [
        lambda c, p: _motion("Mirror picomotors (8742)", c.get(PicomotorViewModel),
                             PicomotorControls, p),
        lambda c, p: _motion("ELL14 rotator", c.get(ELL14RotatorViewModel),
                             MotionControls, p),
        # Last in its group, and with its own class: moving this plate by hand while the
        # phase loop is driving it is two controllers fighting over one optic, so every
        # move here is gated by a confirmation that stops the loop first.
        lambda c, p: _motion("RGV100BL HWP", c.get(RgvViewModel), RgvControls, p),
    ]),
    ("Stages", [
        lambda c, p: _motion("MFA-CC — centrifuge delay", c.get(MfaccViewModel),
                             MotionControls, p),
        lambda c, p: _motion("UTS150CC — grating", c.get(Uts150ccViewModel),
                             MotionControls, p),
        lambda c, p: _motion("FMS300PP — probe", c.get(Fms300ppViewModel),
                             MotionControls, p),
    ]),
]


def _block(title: str, vm: object, controls: QWidget, parent: QWidget, *,
           footer: QWidget | None = None) -> QGroupBox:
    """One device on the page: title, centered worker bar, controls, optional Apply.

    The same parts in the same order as the popout, because both are a ``DeviceFrame`` --
    the popout's is scrollable and pinned inside its window chrome, this one is not and
    the group page scrolls instead.
    """
    box = QGroupBox(title, parent)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)

    frame = DeviceFrame(box, scrollable=False)
    frame.add_worker_controls(vm)
    frame.body_layout.addWidget(controls)
    if footer is not None:
        frame.footer_layout.addStretch(1)
        frame.footer_layout.addWidget(footer)
        frame.footer_widget.setVisible(True)
    lay.addWidget(frame)
    return box


def _form_block(title: str, vm: object, form_cls: type[DirtyFormWidget],
                parent: QWidget) -> QGroupBox:
    """A settings-form device, whose Apply goes in the block's footer."""
    form = form_cls(vm, parent)
    return _block(title, vm, form, parent, footer=form.apply_button)


def _motion(title: str, vm: object, controls_cls: type[QWidget],
            parent: QWidget) -> QGroupBox:
    """A motion device, which has no Apply -- its controls act immediately."""
    return _block(title, vm, controls_cls(vm, parent), parent)


class DevicesView(QWidget):
    def __init__(self, container: Container, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # QTabBar over a QStackedWidget rather than a QTabWidget: QTabWidget would put a
        # framed pane around each page, and each page here is already a frameless scroll
        # area sitting directly on the dock background.
        self._tabs = QTabBar()
        self._tabs.setDrawBase(False)
        self._stack = QStackedWidget()

        for label, builders in _GROUPS:
            self._tabs.addTab(label)
            self._stack.addWidget(self._build_page(container, builders))

        self._tabs.currentChanged.connect(self._stack.setCurrentIndex)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # No stretch on the tab bar and stretch=1 on the stack: the stack is the only
        # child that grows, so the tabs stay pinned at the top whatever the dock height
        # and however far the group below is scrolled. Same arrangement PanelView uses
        # for the popouts' Start/Pause header.
        outer.addWidget(self._tabs)
        outer.addWidget(self._stack, stretch=1)

    def _build_page(self, container: Container,
                    builders: list[Callable[[Container, QWidget], QWidget]]) -> QScrollArea:
        body = QWidget()
        stack = QVBoxLayout(body)
        stack.setContentsMargins(8, 8, 8, 8)
        stack.setSpacing(10)
        for build in builders:
            stack.addWidget(build(container, body))
        # Keeps the blocks at their natural height and pinned to the top when the group
        # is shorter than the dock, instead of stretching them to fill it.
        stack.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        return scroll
