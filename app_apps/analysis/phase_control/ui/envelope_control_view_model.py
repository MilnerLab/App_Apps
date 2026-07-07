from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from app_apps.analysis.phase_control.events import EnvelopeStateChanged

if TYPE_CHECKING:
    import pyqtgraph as pg
    from app_apps.analysis.phase_control.envelope_handle import EnvelopeHandle


class EnvelopeControlViewModel(QObject):
    worker_state_changed = Signal(object)  # WorkerStatus

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: EnvelopeHandle,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._dispatcher = dispatcher
        self._handle = handle
        self._plot_item: pg.PlotItem | None = None
        self._unsub = bus.subscribe(EnvelopeStateChanged, self._on_state_changed)

    def set_chart(self, plot_item: pg.PlotItem) -> None:
        self._plot_item = plot_item

    @property
    def worker_state(self) -> WorkerStatus:
        return self._handle.state

    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def resume(self) -> None:
        self._handle.resume()

    def stop(self) -> None:
        self._handle.stop()

    def _on_state_changed(self, _: EnvelopeStateChanged) -> None:
        state = self._handle.state
        self._dispatcher.post(lambda: self.worker_state_changed.emit(state))
