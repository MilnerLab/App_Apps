from __future__ import annotations

from base_core.framework.app.app_message import AppMessage, MessageLevel
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.subprocess.subprocess_service import SubprocessService
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from base_core.framework.subprocess.shared_memory.shared_memory_base_messages import ItemAvailable
from base_core.framework.subprocess.shared_memory.shared_buffer_coordinator import (
    SharedBufferCoordinator,
)
from base_core.framework.subprocess.worker_handle import WorkerHandle
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer

from app_apps.io.spectrometer.events import SpectrumAvailable


WORKER_NAME = "spectrometer"


class SpectrometerService(SubprocessService):
    """
    Main-process handle to the SPM-002 spectrometer subprocess.

    Registers a WorkerHandle with an output buffer for the spectrometer worker
    and translates ItemAvailable("ui") → SpectrumAvailable for in-process consumers.

    Consumers:
      - Subscribe to SpectrumAvailable on the EventBus.
      - Read wavelengths/intensities via buffer.intensities_view(msg.slot).
      - Call svc.ack_slot(msg.slot, msg.item_id, "ui") when done.
    """

    def __init__(
        self,
        io: TaskRunner,
        endpoint: JsonlSubprocessEndpoint,
        bus: EventBus,
        buffer: SharedSpectrumBuffer,
        coordinator: SharedBufferCoordinator,
    ) -> None:
        super().__init__(io=io, endpoint=endpoint, bus=bus)
        self._register_handle(
            WORKER_NAME,
            WorkerHandle(service=self, name=WORKER_NAME, bus=bus).with_output(buffer, coordinator),
        )
        self._sub = None

    def start(self) -> None:
        super().start()
        self._sub = self._bus.subscribe(ItemAvailable, self._on_item_available)
        self.worker(WORKER_NAME).start_async(
            key="spectrometer.worker.start",
            on_error=lambda exc: self._bus.publish(
                AppMessage(f"Spectrometer failed to start: {exc}", MessageLevel.ERROR)
            ),
        )

    def stop(self) -> None:
        self.worker(WORKER_NAME).stop()
        if self._sub is not None:
            self._sub()
            self._sub = None
        super().stop()

    def ack_slot(self, slot: int, item_id: int, consumer_id: str) -> None:
        self.worker(WORKER_NAME).ack_slot(slot=slot, item_id=item_id, consumer_id=consumer_id)

    def _on_item_available(self, msg: ItemAvailable) -> None:
        if msg.consumer_id != "ui" or msg.buffer_id != WORKER_NAME:
            return
        self._bus.publish(SpectrumAvailable(
            slot=msg.slot,
            item_id=msg.item_id,
            timestamp_ns=msg.timestamp_ns,
        ))
