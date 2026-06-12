from __future__ import annotations

from typing import Callable, TYPE_CHECKING

from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.shm.writer_service import WriterSubprocessService
from base_core.ipc.subprocess_service import SubprocessService
from app_apps.io.spectrometer.buffer import SpectrumBuffer, SpectrumMemorySpec
from app_apps.analysis.phase_control.subprocess.messages import ProcessSpectrum
from app_apps.io.spectrometer.events import SpectrumAvailable


class PhaseControlService(SubprocessService):
    """
    Main-process service for the phase control subprocess.

    Read-only consumer of SpectrumBuffer. Registers itself as a consumer on the
    writer service's SlotCoordinator when started, and unregisters on stop so no
    slot gets stuck waiting for acks from a stopped consumer.

    Forwards each SpectrumAvailable to the subprocess as ProcessSpectrum so both
    workers (PhaseTrackingWorker, EnvelopeWorker) can process the same slot.
    """

    CONSUMER_ID = "phase_control"

    def __init__(
        self,
        bus: EventBus,
        io: TaskRunner,
        spec: SpectrumMemorySpec,
        writer_service: WriterSubprocessService,
    ) -> None:
        super().__init__(bus, io)
        self.add_buffer(SpectrumBuffer, spec)
        self._writer_service = writer_service
        self._spectrum_unsub: Callable[[], None] | None = None

    @property
    def _entry_module(self) -> str:
        return "app_apps.analysis.phase_control.subprocess.phase_control_process"

    def start(self) -> None:
        self._writer_service.register_consumer(self.CONSUMER_ID)
        super().start()
        self._spectrum_unsub = self._bus.subscribe(SpectrumAvailable, self._on_spectrum_available)

    def stop(self) -> None:
        if self._spectrum_unsub is not None:
            self._spectrum_unsub()
            self._spectrum_unsub = None
        super().stop()
        self._writer_service.unregister_consumer(self.CONSUMER_ID)

    def _on_spectrum_available(self, event: SpectrumAvailable) -> None:
        self.emit(ProcessSpectrum(
            slot=event.slot,
            item_id=event.item_id,
            timestamp_ns=event.timestamp_ns,
        ))
