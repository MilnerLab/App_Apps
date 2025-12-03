from __future__ import annotations
from PySide6.QtCore import QObject
from LabviewToPython.core.events.eventbus import IEventBus
from LabviewToPython.services.interfaces import IPressureService, IDataStore

class PressureViewModel(QObject):
    def __init__(self, bus: IEventBus, pressure: IPressureService, store: IDataStore) -> None:
        super().__init__()
        self._bus = bus
        self._pressure = pressure
        self._store = store

    @property
    def title(self) -> str:
        return "Pressure"
