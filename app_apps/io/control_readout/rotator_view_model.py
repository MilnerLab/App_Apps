from __future__ import annotations

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import BaseWorkerHandle, WorkerStatus
from base_core.math.models import Angle
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread


class RotatorViewModel(PanelViewModel):
    """Shared ViewModel base for motorized-waveplate rotator devices
    (ELL14, RGV100BL, ...).

    Subclasses subscribe to their device's angle/state-changed events in
    __init__ (calling ``self._sub(..., self._on_state_changed)`` for the
    device's worker-state event, and their own handler for angle updates
    that emits ``self.angle_updated``), and implement ``rotate()``/``home()``
    by publishing the device-specific command.
    """

    angle_updated = Signal(float)          # degrees
    worker_state_changed = Signal(object)  # WorkerStatus

    def __init__(self, bus: EventBus, dispatcher: QtDispatcher, handle: BaseWorkerHandle) -> None:
        super().__init__(bus, dispatcher)
        self._handle = handle

    @property
    def worker_status(self) -> WorkerStatus:
        return self._handle.state

    @ui_thread
    def _on_state_changed(self, _event: object) -> None:
        self.worker_state_changed.emit(self._handle.state)

    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def resume(self) -> None:
        self._handle.resume()

    def stop(self) -> None:
        self._handle.stop()

    def rotate(self, angle: Angle) -> None:
        raise NotImplementedError

    def home(self) -> None:
        raise NotImplementedError
