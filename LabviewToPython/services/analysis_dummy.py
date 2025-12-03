from __future__ import annotations
from LabviewToPython.services.interfaces import IAnalysisService
from LabviewToPython.core.events.eventbus import IEventBus

class DummyAnalysisService(IAnalysisService):
    def __init__(self, bus: IEventBus) -> None:
        self._bus = bus

    def submit_frame(self, frame: object) -> None:
        # No-op; in a real impl, compute cos^2(theta) and publish results
        self._bus.publish("analysis/result", {"ok": True})
