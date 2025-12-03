from __future__ import annotations
from typing import Any, Dict
from LabviewToPython.services.interfaces import IDataStore
from LabviewToPython.core.events.eventbus import IEventBus

class DummyDataStore(IDataStore):
    def __init__(self, bus: IEventBus) -> None:
        self._bus = bus

    def write_meta(self, record: Dict[str, Any]) -> None:
        # Scaffold: acknowledge write
        self._bus.publish("store/meta", {"written": True})

    def write_result(self, record: Dict[str, Any]) -> None:
        self._bus.publish("store/result", {"written": True})
