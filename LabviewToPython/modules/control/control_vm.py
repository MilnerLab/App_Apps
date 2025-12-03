from __future__ import annotations
from PySide6.QtCore import QObject
from LabviewToPython.core.events.eventbus import IEventBus
from LabviewToPython.services.interfaces import IMotionService, IPumpService

class ControlViewModel(QObject):
    def __init__(self, bus: IEventBus, motion: IMotionService, pump: IPumpService) -> None:
        super().__init__()
        self._bus = bus
        self._motion = motion
        self._pump = pump

    @property
    def title(self) -> str:
        return "Control"
