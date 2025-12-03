from __future__ import annotations
from PySide6.QtCore import QObject
from LabviewToPython.core.events.eventbus import IEventBus
from LabviewToPython.services.interfaces import IScanScheduler, IDataStore

class ScanViewModel(QObject):
    def __init__(self, bus: IEventBus, scheduler: IScanScheduler, store: IDataStore) -> None:
        super().__init__()
        self._bus = bus
        self._scheduler = scheduler
        self._store = store

    @property
    def title(self) -> str:
        return "Scan"
