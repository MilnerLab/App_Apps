from __future__ import annotations

from base_core.framework.events import EventBus
from base_core.math.enums import AngleUnit
from base_core.math.models import Angle
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_view_model import ui_thread

from app_apps.io.control_readout.ell14.handler import ELL14RotatorHandle
from app_apps.io.control_readout.ell14.events import (
    ELL14RotatorHomed,
    ELL14WorkerStateChanged,
    NewELL14Angle,
    RequestRotate,
)
from app_apps.io.control_readout.ui.motion_view_model import MotionViewModel


class ELL14RotatorViewModel(MotionViewModel):
    """The ELL14 rotary mount.

    Relative moves go out as ``RequestRotate`` -- the device's native contract -- rather than
    through ``MotionViewModel``'s synthesised ``move_to(position + delta)``. That is the one
    device where the relative path is the real one and the absolute path is the synthetic
    one, so it is the one device where relative must NOT require a known angle first.
    """

    units = "deg"
    decimals = 3
    limits = (-360.0, 360.0)
    default_step = 1.0

    def __init__(self, bus: EventBus, dispatcher: QtDispatcher,
                 handle: ELL14RotatorHandle) -> None:
        super().__init__(bus, dispatcher, handle, ELL14WorkerStateChanged, NewELL14Angle,
                         read_position=lambda e: e.angle.Deg)
        self._sub(ELL14RotatorHomed, self._on_homed)

    @property
    def device_name(self) -> str:
        return "ELL14 rotator"

    def move_relative(self, delta: float) -> None:
        if not self._allow_move(f"rotate by {delta:+.{self.decimals}f} deg"):
            return
        self.rotate(Angle(float(delta), AngleUnit.DEG, wrap=False))

    def rotate(self, angle: Angle) -> None:
        """Relative rotation. Kept as the public name it has always had."""
        self._bus.publish(RequestRotate(angle=angle, sign=1))

    @ui_thread
    def _on_homed(self, _event: ELL14RotatorHomed) -> None:
        self._msg("Rotator homed", MessageLevel.INFO)
