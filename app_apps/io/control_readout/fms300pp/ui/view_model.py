from __future__ import annotations

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher

from app_apps.io.control_readout.fms300pp.handler import Fms300ppHandle
from app_apps.io.control_readout.fms300pp.events import (
    Fms300ppWorkerStateChanged,
    NewFms300ppPosition,
)
from app_apps.io.control_readout.ui.motion_view_model import MotionViewModel


class Fms300ppViewModel(MotionViewModel):
    """Probe delay. Deliberately NOT a phase-template invalidation trigger: moving the
    probe does not change the interferogram's shape.

    Everything except the travel limits comes from ``MotionViewModel``; see it for why a
    relative move is refused until the position has been read.
    """

    units = "mm"
    decimals = 4
    limits = (0.0, 300.0)
    default_step = 0.1

    def __init__(self, bus: EventBus, dispatcher: QtDispatcher, handle: Fms300ppHandle) -> None:
        super().__init__(bus, dispatcher, handle, Fms300ppWorkerStateChanged, NewFms300ppPosition)
