from __future__ import annotations
from typing import Dict, Any
from LabviewToPython.services.interfaces import IScanScheduler
from LabviewToPython.core.events.eventbus import IEventBus

class DummyScanScheduler(IScanScheduler):
    def __init__(self, bus: IEventBus) -> None:
        self._bus = bus
        self._active = False

    def schedule(self, plan: Dict[str, Any]) -> None:
        self._active = True
        # Scaffold: immediately publish a "done"
        self._bus.publish("scan/status", {"state": "done"})
        self._active = False

    def stop(self) -> None:
        self._active = False
        self._bus.publish("scan/status", {"state": "stopped"})
