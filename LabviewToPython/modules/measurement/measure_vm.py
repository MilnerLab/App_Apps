from __future__ import annotations
from PySide6.QtCore import QObject
from LabviewToPython.core.events.eventbus import IEventBus
from LabviewToPython.services.interfaces import ICameraService, IAnalysisService, IDataStore

class MeasureViewModel(QObject):
    """Bind camera/analysis/store services; no UI code here."""
    def __init__(self, bus: IEventBus) -> None:
        super().__init__()
        self._bus = bus

    @property
    def title(self) -> str:
        return "Measurement"
