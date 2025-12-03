from __future__ import annotations
from typing import Dict, Any
from LabviewToPython.services.interfaces import IPumpService
from LabviewToPython.core.events.eventbus import IEventBus

class DummyPumpService(IPumpService):
    def __init__(self, bus: IEventBus) -> None:
        self._bus = bus
        self._running = False

    def start(self) -> None:
        self._running = True
        self._bus.publish("pump/status", {"running": True})

    def stop(self) -> None:
        self._running = False
        self._bus.publish("pump/status", {"running": False})

    def read_status(self) -> Dict[str, Any]:
        return {"running": self._running}
