from __future__ import annotations

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_core.math.models import Angle
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread

from app_apps.io.control_readout.ell14.handler import ELL14RotatorHandle
from app_apps.io.control_readout.ell14.events import (
    ELL14RotatorHomed,
    ELL14WorkerStateChanged,
    NewELL14Angle,
    RequestRotate,
)


class ELL14RotatorViewModel(PanelViewModel):
    angle_updated = Signal(float)          # degrees
    worker_state_changed = Signal(object)  # emits WorkerStatus

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: ELL14RotatorHandle,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._handle = handle
        self._sub(NewELL14Angle, self._on_angle_updated)
        self._sub(ELL14RotatorHomed, self._on_homed)
        self._sub(ELL14WorkerStateChanged, self._on_state_changed)

    @property
    def worker_status(self) -> WorkerStatus:
        return self._handle.state

    @ui_thread
    def _on_state_changed(self, _: ELL14WorkerStateChanged) -> None:
        self.worker_state_changed.emit(self._handle.state)

    @ui_thread
    def _on_angle_updated(self, event: NewELL14Angle) -> None:
        self.angle_updated.emit(event.angle.Deg)

    @ui_thread
    def _on_homed(self, _event: ELL14RotatorHomed) -> None:
        self.angle_updated.emit(0.0)
        self._msg("Rotator homed", MessageLevel.INFO)

    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def resume(self) -> None:
        self._handle.resume()

    def stop(self) -> None:
        self._handle.stop()

    def rotate(self, angle: Angle) -> None:
        self._bus.publish(RequestRotate(angle=angle, sign=1))

    def home(self) -> None:
        self._handle.home()
        self._msg("Homing rotator…", MessageLevel.INFO)
