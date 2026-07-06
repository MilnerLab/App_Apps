from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from app_apps.analysis.phase_control.events import EnvelopeStateChanged

if TYPE_CHECKING:
    from PySide6.QtCharts import QChart
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
        self._chart: QChart | None = None
        self._unsub = bus.subscribe(EnvelopeStateChanged, self._on_state_changed)

    def set_chart(self, chart: QChart) -> None:
        self._chart = chart

    @property
    def worker_state(self) -> WorkerStatus:
        return self._handle.state

    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def reset(self) -> None:
        self._handle.reset()

    def _on_state_changed(self, _: EnvelopeStateChanged) -> None:
        state = self._handle.state
        self._dispatcher.post(lambda: self.worker_state_changed.emit(state))
