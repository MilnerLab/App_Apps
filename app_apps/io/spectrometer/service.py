from __future__ import annotations

from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.shm.slot_coordinator import SlotCoordinator
from base_core.framework.shm.writer_service import WriterSubprocessService
from app_apps.io.spectrometer.buffer import SpectrumBuffer, SpectrumMemorySpec
from app_apps.io.spectrometer.events import SpectrumAvailable, SpectrumAck


class SpectrometerService(WriterSubprocessService[SpectrumAvailable, SpectrumAck]):
    """
    Main-process service for the spectrometer subprocess.

    Owns the SpectrumBuffer shared memory region, the SlotCoordinator, and the
    subprocess lifecycle. Runs the subprocess using the 32-bit Python interpreter
    so PhotonSpectr.dll (x86) can be loaded.
    """

    def __init__(
        self,
        bus: EventBus,
        io: TaskRunner,
        spec: SpectrumMemorySpec,
        python_exe: str | None = None,
    ) -> None:
        coordinator: SlotCoordinator[SpectrumAvailable, SpectrumAck] = SlotCoordinator(
            spec=spec,
            owner_id="spectrometer",
            bus=bus,
            make_available=lambda slot, item_id, ts: SpectrumAvailable(
                slot=slot, item_id=item_id, timestamp_ns=ts
            ),
            ack_type=SpectrumAck,
        )
        super().__init__(bus, io, SpectrumBuffer, spec, coordinator, python_exe=python_exe)

    @property
    def _entry_module(self) -> str:
        return "spm_002.spectrometer_process"
