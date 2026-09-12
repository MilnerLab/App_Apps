from __future__ import annotations

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.device_panel_view_model import DevicePanelViewModel
from base_qt.ui.panel_view_model import ui_thread
from oscilloscope.config import ScopeConfig

from app_apps.io.oscilloscope.events import (
    OscilloscopeConfigChanged,
    OscilloscopeTimebaseChanged,
    OscilloscopeWorkerStateChanged,
)
from app_apps.io.oscilloscope.oscilloscope_worker_handler import OscilloscopeWorkerHandle


class OscilloscopeViewModel(DevicePanelViewModel):
    """Controls and settings for the scope. No trace.

    Deliberately not a shared-memory consumer: the live trace belongs to the alignment
    view, and a second consumer here would make every frame wait on this panel's ack for
    no gain.
    """

    worker_state_changed = Signal(object)  # emits WorkerStatus
    timebase_changed = Signal()             # the instrument reported a new sample interval
    #: The config was written back. This VM is a singleton shared by the popout and the
    #: Devices page, and both bind forms to the one config object -- so whichever of them
    #: applied, the other has to re-read or it keeps showing the old numbers.
    config_updated = Signal()

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: OscilloscopeWorkerHandle,
        config: ScopeConfig,
    ) -> None:
        super().__init__(bus, dispatcher, handle)
        self._handle = handle
        self._config = config
        self._sub(OscilloscopeWorkerStateChanged, self._on_state_changed)
        self._sub(OscilloscopeTimebaseChanged, self._on_timebase_changed)

    @property
    def config(self) -> ScopeConfig:
        return self._config

    @property
    def worker_status(self) -> WorkerStatus:
        return self._handle.state

    @property
    def dt_s(self) -> float:
        """Sample interval the instrument reported, 0.0 before the first trace."""
        return self._handle.dt_s

    @ui_thread
    def _on_state_changed(self, _: OscilloscopeWorkerStateChanged) -> None:
        self.worker_state_changed.emit(self._handle.state)

    @ui_thread
    def _on_timebase_changed(self, _: OscilloscopeTimebaseChanged) -> None:
        self.timebase_changed.emit()

    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def resume(self) -> None:
        self._handle.resume()

    def stop(self) -> None:
        self._handle.stop()

    def set_config(self) -> None:
        self._bus.publish(OscilloscopeConfigChanged())
        self.config_updated.emit()
