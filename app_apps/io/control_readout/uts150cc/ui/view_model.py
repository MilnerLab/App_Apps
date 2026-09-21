from __future__ import annotations

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher

from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle
from app_apps.io.control_readout.uts150cc.events import (
    Uts150ccWorkerStateChanged,
    NewUts150ccPosition,
)
from app_apps.io.control_readout.ui.motion_view_model import MotionViewModel


class Uts150ccViewModel(MotionViewModel):
    """Grating position. A commanded move here invalidates the frozen phase template
    (PhaseStabilizationHandle subscribes to RequestMoveUts150cc) -- the chirp moves with
    the grating.

    Everything except the travel limits comes from ``MotionViewModel``; see it for why a
    relative move is refused until the position has been read.
    """

    units = "mm"
    decimals = 4
    limits = (0.0, 150.0)
    default_step = 0.1

    def __init__(self, bus: EventBus, dispatcher: QtDispatcher, handle: Uts150ccHandle) -> None:
        super().__init__(bus, dispatcher, handle, Uts150ccWorkerStateChanged, NewUts150ccPosition)
