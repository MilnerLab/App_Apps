from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.subprocess_service import SubprocessService


class OscilloscopeService(SubprocessService):
    """
    Main-process service for the oscilloscope subprocess.

    Runs in the normal interpreter (no 32-bit DLL needed, unlike spectrometer).
    Buffer ownership and slot coordination live in OscilloscopeWorkerHandle.
    """

    def __init__(self, bus: EventBus) -> None:
        super().__init__(bus)

    @property
    def _entry_module(self) -> str:
        return "oscilloscope.oscilloscope_process"
