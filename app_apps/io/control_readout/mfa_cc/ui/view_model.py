from __future__ import annotations

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher

from app_apps.io.control_readout.mfa_cc.handler import MfaccHandle
from app_apps.io.control_readout.mfa_cc.events import (
    MfaccWorkerStateChanged,
    NewMfaccPosition,
)
from app_apps.io.control_readout.ui.motion_view_model import MotionViewModel


class MfaccViewModel(MotionViewModel):
    """Pump-probe delay. A commanded move here invalidates the frozen phase template
    (PhaseStabilizationHandle subscribes to RequestMoveMfacc) -- the fringe shape moves
    with the delay.

    Everything except the travel limits comes from ``MotionViewModel``; see it for why a
    relative move is refused until the position has been read.
    """

    units = "mm"
    decimals = 4
    limits = (0.0, 25.0)
    default_step = 0.01

    def __init__(self, bus: EventBus, dispatcher: QtDispatcher, handle: MfaccHandle) -> None:
        super().__init__(bus, dispatcher, handle, MfaccWorkerStateChanged, NewMfaccPosition)
