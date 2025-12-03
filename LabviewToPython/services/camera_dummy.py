from __future__ import annotations
from typing import Any
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from LabviewToPython.core.events.eventbus import IEventBus
from LabviewToPython.services.interfaces import ICameraService


class _CameraWorker(QObject):
    tick = Signal()

    def __init__(self, bus: IEventBus) -> None:
        super().__init__()
        self._bus = bus
        self._timer: QTimer | None = None

    def start_timer(self, ms: int = 500) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timeout)
        self._timer.start(ms)

    def stop_timer(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    def _on_timeout(self) -> None:
        # Publish a lightweight heartbeat instead of real frames (scaffold)
        self._bus.publish("camera/heartbeat", {"ok": True})


class DummyCameraService(ICameraService):
    """
    Minimal camera service:
    - Opens/closes nothing.
    - When started, emits a periodic 'camera/heartbeat' on the event bus.
    """
    def __init__(self, bus: IEventBus) -> None:
        self._bus = bus
        self._thread = QThread()
        self._worker = _CameraWorker(bus)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(lambda: self._worker.start_timer(800))

    def open(self) -> None:
        pass

    def start(self) -> None:
        if not self._thread.isRunning():
            self._thread.start()

    def stop(self) -> None:
        if self._thread.isRunning():
            self._worker.stop_timer()
            self._thread.quit()
            self._thread.wait()

    def close(self) -> None:
        pass

    def set_params(self, **params: Any) -> None:
        _ = params  # no-op in dummy
