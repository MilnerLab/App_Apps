from __future__ import annotations

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread

from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle
from app_apps.io.control_readout.uts150cc.events import Uts150ccWorkerStateChanged


class Uts150ccViewModel(PanelViewModel):
    worker_state_changed = Signal(object)  # emits WorkerStatus

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: Uts150ccHandle,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._handle = handle
        self._sub(Uts150ccWorkerStateChanged, self._on_state_changed)

    @property
    def worker_status(self) -> WorkerStatus:
        return self._handle.state

    @ui_thread
    def _on_state_changed(self, _: Uts150ccWorkerStateChanged) -> None:
        self.worker_state_changed.emit(self._handle.state)

    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def reset(self) -> None:
        self._handle.reset()
