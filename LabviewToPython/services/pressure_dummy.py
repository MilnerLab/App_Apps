from __future__ import annotations
from PySide6.QtCore import QObject, QThread, QTimer
from LabviewToPython.core.events.eventbus import IEventBus
from LabviewToPython.services.interfaces import IPressureService

class _PressureWorker(QObject):
    def __init__(self, bus: IEventBus) -> None:
        super().__init__()
        self._bus = bus
        self._timer: QTimer | None = None
        self._value = 1.0e-6  # Pa (arbitrary scaffold)

    def start(self, interval_ms: int) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(interval_ms)

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    def _tick(self) -> None:
        # Publish synthetic value
        self._value *= 1.0  # keep constant in scaffold
        self._bus.publish("pressure/value", {"channel": "dummy", "value": self._value, "unit": "Pa"})

class DummyPressureService(IPressureService):
    def __init__(self, bus: IEventBus) -> None:
        self._bus = bus
        self._thread = QThread()
        self._worker = _PressureWorker(bus)
        self._worker.moveToThread(self._thread)

    def start_polling(self, interval_ms: int = 500) -> None:
        self._thread.started.connect(lambda: self._worker.start(interval_ms))
        if not self._thread.isRunning():
            self._thread.start()

    def stop_polling(self) -> None:
        self._worker.stop()
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait()
