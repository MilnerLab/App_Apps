from __future__ import annotations

from PySide6.QtWidgets import QWidget

from base_qt.ui.panel_view import PanelView

from app_apps.io.control_readout.ui.motion_controls import MotionControls
from app_apps.io.control_readout.ui.stabilization_interlock import confirm_stop_stabilization
from app_apps.io.control_readout.ell14.ui.view_model import ELL14RotatorViewModel

TITLE = "ELL14 Rotator"


def _release_hook(vm: ELL14RotatorViewModel, move: object) -> None:
    """Clear the interlock hook a destroyed ``ELL14Controls`` installed, if still current.
    ``==`` not ``is`` -- see ``rgv.ui.view._release_hooks``."""
    if vm.confirm_move == move:
        vm.confirm_move = None


class ELL14Controls(MotionControls):
    """``MotionControls`` plus the stabilization interlock, for when the phase loop has been
    switched onto this rotator."""

    def __init__(self, vm: ELL14RotatorViewModel, parent: QWidget | None = None) -> None:
        super().__init__(vm, parent)
        self._ell14_vm = vm
        vm.confirm_move = self._confirm_move
        hooks = (vm, self._confirm_move)
        self.destroyed.connect(lambda *_: _release_hook(*hooks))

    def _confirm_move(self, description: str) -> bool:
        return confirm_stop_stabilization(self, self._ell14_vm, description)


class ELL14RotatorView(PanelView):
    """The floating Devices-menu popout. The panel embeds the same ``MotionControls``
    block directly (as ``ELL14Controls``), so there is one implementation of the controls.

    No ``vm=``: the view model is a singleton shared with the Devices page, so closing
    this popout must hide it rather than tear those subscriptions down."""

    def __init__(self, vm: ELL14RotatorViewModel, parent: QWidget) -> None:
        super().__init__(TITLE, parent)
        self._vm = vm
        self.add_worker_controls(vm)
        self.body_layout.addWidget(ELL14Controls(vm, self))
