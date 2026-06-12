from __future__ import annotations

from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.shm.slot_coordinator import SlotCoordinator
from base_core.framework.shm.writer_service import WriterSubprocessService
from app_apps.io.control_readout.buffer import PressureBuffer, PressureMemorySpec
from app_apps.io.control_readout.events import PressureAvailable, PressureAck


class ControlReadoutService(WriterSubprocessService[PressureAvailable, PressureAck]):
    """
    Main-process service for the control readout subprocess.

    Owns the PressureBuffer shared memory region and the SlotCoordinator.
    Hosts the rotator worker (ELL14 HWP) and will host pressure-sensor workers
    when implemented.
    """

    def __init__(
        self,
        bus: EventBus,
        io: TaskRunner,
        spec: PressureMemorySpec,
    ) -> None:
        coordinator: SlotCoordinator[PressureAvailable, PressureAck] = SlotCoordinator(
            spec=spec,
            owner_id="control_readout",
            bus=bus,
            make_available=lambda slot, item_id, ts: PressureAvailable(
                slot=slot, item_id=item_id, timestamp_ns=ts
            ),
            ack_type=PressureAck,
        )
        super().__init__(bus, io, PressureBuffer, spec, coordinator)

    @property
    def _entry_module(self) -> str:
        return "control_readout.control_readout_process"
