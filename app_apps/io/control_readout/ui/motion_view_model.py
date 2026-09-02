"""One view model behind every positioned device in the Devices panel.

The three ESP301 linear stages (UTS150CC, MFA-CC, FMS300PP), the RGV100BL rotator and the
ELL14 rotator all expose the same four operations -- read, move absolutely, move relatively,
home -- through handles with the same shape. Writing that five times would be five places to
fix the one thing that is actually subtle here, which is what a relative move does when the
position is not known yet.

Relative moves are synthesised from ``move_to(position + delta)``. The device layer has no
relative-move message for the ESP301 stages, and adding one means a Devices-repo change; the
panel is App-side only. The consequence is that a relative move is IMPOSSIBLE until a
position has been read, and the view model says so rather than moving from an assumed zero
-- which on a stage with 150 mm of travel is how you drive into a hard stop.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread

log = logging.getLogger(__name__)


class MotionViewModel(PanelViewModel):
    """Read / absolute / relative / home over a handle with ``move_to``, ``home`` and
    ``get_position``.

    Subclasses supply the handle and the two events, and override ``units``/``decimals``
    and ``limits`` for the device's own travel.
    """

    position_changed = Signal(object)      # float, or None when the position is unknown
    worker_state_changed = Signal(object)  # WorkerStatus

    #: Displayed unit and the precision the readout is meaningful to.
    units = "mm"
    decimals = 4
    #: Travel limits for the absolute spinbox. Advisory: the device enforces its own.
    limits: tuple[float, float] = (-1000.0, 1000.0)
    #: Default relative step, in ``units``.
    default_step = 0.1

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: Any,
        state_event: type,
        position_event: type,
        read_position: Callable[[Any], float] = lambda e: e.position,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._handle = handle
        self._read_position = read_position
        self._position: float | None = None
        #: Veto called before every move, with a short description of it. The view installs
        #: one when the device needs a confirmation dialog; only the RGV does, because only
        #: the RGV is a plate the control loop is also driving. Returning False cancels.
        self.confirm_move: Callable[[str], bool] | None = None
        self._sub(state_event, self._on_state_changed)
        self._sub(position_event, self._on_position)

    # -- state ------------------------------------------------------------------------
    @property
    def worker_status(self) -> WorkerStatus:
        return self._handle.state

    @property
    def position(self) -> float | None:
        """Last known position, or None if it has never been read."""
        return self._position

    @ui_thread
    def _on_state_changed(self, _event: object) -> None:
        state = self._handle.state
        # Read the position the moment the device comes up, so the panel shows where the
        # stage IS without the operator having to move it to find out -- and, more to the
        # point, so the first relative move is possible without pressing Read first.
        if state == WorkerStatus.RUNNING and self._position is None:
            self.refresh()
        self.worker_state_changed.emit(state)

    @ui_thread
    def _on_position(self, event: object) -> None:
        self._position = float(self._read_position(event))
        self.position_changed.emit(self._position)

    # -- commands ---------------------------------------------------------------------
    def refresh(self) -> None:
        """Re-read the position without moving anything.

        A no-op unless the device is running. The Devices panel is built at app start, when
        the workers may not be up yet and the handle has no connector at all -- and asking a
        stopped device where it is has no answer anyway.
        """
        if self._handle.state != WorkerStatus.RUNNING:
            return
        self._handle.get_position()

    def move_absolute(self, position: float) -> None:
        if not self._allow_move(f"move to {position:.{self.decimals}f} {self.units}"):
            return
        self._handle.move_to(float(position))

    def move_relative(self, delta: float) -> None:
        base = self._position
        if base is None:
            # Not an error the operator can ignore: moving from an assumed zero on a stage
            # with 150 mm of travel drives it into a hard stop.
            self._msg("Position unknown — press Read before a relative move.",
                      MessageLevel.WARNING)
            self.refresh()
            return
        self.move_absolute(base + float(delta))

    def home(self) -> None:
        if not self._allow_move("home"):
            return
        self._handle.home()
        self._msg(f"Homing {self.device_name}…", MessageLevel.INFO)

    # -- hooks ------------------------------------------------------------------------
    @property
    def device_name(self) -> str:
        return type(self).__name__.replace("ViewModel", "")

    def _allow_move(self, description: str) -> bool:
        return self.confirm_move is None or self.confirm_move(description)

    # -- worker lifecycle -------------------------------------------------------------
    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def resume(self) -> None:
        self._handle.resume()

    def stop(self) -> None:
        self._handle.stop()
