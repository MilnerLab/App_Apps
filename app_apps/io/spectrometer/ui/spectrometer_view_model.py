from __future__ import annotations

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread
from spm_002.config import SpectrometerConfig

from app_apps.io.spectrometer.events import SpectrometerConfigChanged, SpectrometerWorkerStateChanged
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle


class SpectrometerViewModel(PanelViewModel):
    worker_state_changed = Signal(object)  # emits WorkerStatus

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: SpectrometerWorkerHandle,
        config: SpectrometerConfig,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._handle = handle
        self._config = config
        self._sub(SpectrometerWorkerStateChanged, self._on_state_changed)

    @property
    def config(self) -> SpectrometerConfig:
        return self._config

    @property
    def worker_status(self) -> WorkerStatus:
        return self._handle.state

    @ui_thread
    def _on_state_changed(self, _: SpectrometerWorkerStateChanged) -> None:
        self.worker_state_changed.emit(self._handle.state)

    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def resume(self) -> None:
        self._handle.resume()

    def stop(self) -> None:
        self._handle.stop()

    def set_config(self) -> None:
        self._bus.publish(SpectrometerConfigChanged())
