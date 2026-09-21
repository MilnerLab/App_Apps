from __future__ import annotations

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread

from app_apps.io.control_readout.rgv.handler import RgvHandle
from app_apps.io.control_readout.rgv.events import (
    NewRGVAngle,
    RgvSpinStateChanged,
    RgvWorkerStateChanged,
)
from app_apps.io.control_readout.ui.motion_view_model import MotionViewModel
from base_qt.ui.panel_view_model import ui_thread


MIN_SPIN_HZ = 0.001 #we don't know what it actually is.
MAX_SPIN_HZ = 2.0 #a physical hardware limit and should be put in a config in the devices repo. It should not be set in the view or view model.
DEFAULT_SPIN_HZ = 0.2
DEG_PER_REV = 360.0

if TYPE_CHECKING:
    from app_apps.analysis.phase_control.service import PhaseControlService


class RgvViewModel(MotionViewModel):
    """The half-wave plate the phase loop turns — so a hand move needs an interlock.

    Moving the RGV while stabilization is running is two controllers fighting over one
    plate: the operator turns it, the loop measures the phase error that creates and turns
    it back. The required behaviour is not "move and hope" -- the loop must be STOPPED
    before the plate is touched.

    The service is injected rather than the stabilization handle, deliberately: the
    envelope worker drives the same plate, and ``PhaseControlService.stop_worker()`` stops
    whichever of the two is active. Interlocking only against the phase worker would leave
    the envelope hill-climb free to fight the operator.

    The confirmation itself is the view's job (it owns the dialog); this exposes the two
    pieces it needs -- whether anything is running, and how to stop it.
    """

    spin_state_changed = Signal(bool, float)  # spinning, rev/s

    units = "deg"
    decimals = 3
    # One full turn. The RGV100BL is a continuous rotator whose controller coordinate is
    # an unbounded running total, so the panel works in ORIENTATION: the device reports
    # position mod 360 and reaches any target by the shortest rotation. A bounded +-168
    # box would have been a lie in both directions -- it hid two thirds of the circle, and
    # it implied an unwind the stage never has to perform.
    limits = (0.0, 360.0)
    default_step = 1.0

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: RgvHandle,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._handle = handle
        self._sub(RgvWorkerStateChanged, self._on_state_changed)

    @property
    def worker_status(self) -> WorkerStatus:
        return self._handle.state

    @ui_thread
    def _on_state_changed(self, _: RgvWorkerStateChanged) -> None:
        self.worker_state_changed.emit(self._handle.state)

    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def resume(self) -> None:
        self._handle.resume()

    def stop(self) -> None:
        self._handle.stop()
