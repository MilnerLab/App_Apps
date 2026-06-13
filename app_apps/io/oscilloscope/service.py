from __future__ import annotations

from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.shm.slot_coordinator import SlotCoordinator
from base_core.framework.shm.writer_service import WriterSubprocessService

from app_apps.io.oscilloscope.buffer import ScopeBuffer, ScopeMemorySpec
from app_apps.io.oscilloscope.events import TraceAvailable, TraceAck


class OscilloscopeService(WriterSubprocessService[TraceAvailable, TraceAck]):
    """
    Main-process service for the oscilloscope subprocess.

    Owns the ScopeBuffer shared-memory region, the SlotCoordinator, and the subprocess
    lifecycle. Runs in the normal interpreter (no 32-bit DLL needed, unlike spectrometer).
    """

    def __init__(
        self,
        bus: EventBus,
        io: TaskRunner,
        spec: ScopeMemorySpec,
    ) -> None:
        coordinator: SlotCoordinator[TraceAvailable, TraceAck] = SlotCoordinator(
            spec=spec,
            owner_id="oscilloscope",
            bus=bus,
            make_available=lambda slot, item_id, ts: TraceAvailable(
                slot=slot, item_id=item_id, timestamp_ns=ts
            ),
            ack_type=TraceAck,
        )
        super().__init__(bus, io, ScopeBuffer, spec, coordinator)

    @property
    def _entry_module(self) -> str:
        return "oscilloscope.oscilloscope_process"
