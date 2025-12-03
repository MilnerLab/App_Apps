from __future__ import annotations
from LabviewToPython.services.interfaces import IMotionService
from LabviewToPython.core.events.eventbus import IEventBus

class DummyMotionService(IMotionService):
    def __init__(self, bus: IEventBus) -> None:
        self._bus = bus
        self._pos = 0.0

    def home(self) -> None:
        self._pos = 0.0
        self._bus.publish("motion/position", {"position": self._pos})

    def move_to(self, position: float) -> None:
        self._pos = float(position)
        self._bus.publish("motion/position", {"position": self._pos})

    def read_position(self) -> float:
        return self._pos
