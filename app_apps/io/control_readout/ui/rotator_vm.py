from __future__ import annotations

import math

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_vm import PanelVM, ui_thread
from base_qt.ui.app_message import MessageLevel

from app_apps.io.control_readout.service import ControlReadoutService
from control_readout.elliptec.messages import HomeRotator, Rotate, RotatorHomed, RotatorMoved


class RotatorVM(PanelVM):
    angle_updated = Signal(float)  # degrees

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        svc: ControlReadoutService,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._svc = svc
        self._sub(RotatorMoved, self._on_moved)
        self._sub(RotatorHomed, self._on_homed)

    @ui_thread
    def _on_moved(self, event: RotatorMoved) -> None:
        self.angle_updated.emit(math.degrees(event.position_rad))

    @ui_thread
    def _on_homed(self, _event: RotatorHomed) -> None:
        self.angle_updated.emit(0.0)
        self._msg("Rotator homed", MessageLevel.INFO)

    def rotate(self, delta_deg: float) -> None:
        self._svc.worker(ControlReadoutService.WORKER_ROTATOR).send(Rotate(angle_rad=math.radians(delta_deg)))

    def home(self) -> None:
        self._svc.worker(ControlReadoutService.WORKER_ROTATOR).send(HomeRotator())
        self._msg("Homing rotator…", MessageLevel.INFO)
