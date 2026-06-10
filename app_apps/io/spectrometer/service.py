from __future__ import annotations

from typing import ClassVar

from base_core.framework.app.app_message import AppMessage, MessageLevel
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.subprocess.subprocess_service import SubprocessService
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from base_core.framework.subprocess.shared_memory.shared_memory_base_messages import (
    ItemAvailable,
)
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

    Consumers register and unregister themselves dynamically via
    add_consumer / remove_consumer (typically called by BufferConsumerMixin
    when a panel opens or closes).  The coordinator is updated accordingly;
    in-flight slots are force-acked on unregister so the buffer never blocks.
    """

    service_name: ClassVar[str] = "spectrometer"

    def __init__(
        self,
        io: TaskRunner,
        endpoint: JsonlSubprocessEndpoint,
        bus: EventBus,
        buffer: SharedSpectrumBuffer,
        coordinator: SharedBufferCoordinator,
    ) -> None:
        super().__init__(io=io, endpoint=endpoint, bus=bus)
        self._buffer = buffer
        self._coordinator = coordinator
        self._register_handle(
            WORKER_NAME,
            WorkerHandle(service=self, name=WORKER_NAME, bus=bus).with_output(buffer, coordinator),
        )
        self._sub = None
        self._consumers: set[str] = set()

    def start(self) -> None:
        super().start()
        self._sub = self._bus.subscribe(ItemAvailable, self._on_item_available)
        self.worker(WORKER_NAME).start_async(
            key="spectrometer.worker.start",
            on_error=lambda exc: self._bus.publish(
                AppMessage(f"Spectrometer failed to start: {exc}", MessageLevel.ERROR)
            ),
        )
        self._publish_status(True)

    def stop(self) -> None:
        self._publish_status(False)
        self.worker(WORKER_NAME).stop()
        if self._sub is not None:
            self._sub()
            self._sub = None
        super().stop()

    # ------------------------------------------------------------------
    # Consumer lifecycle
    # ------------------------------------------------------------------

    def add_consumer(self, consumer_id: str) -> None:
        """Register a new buffer consumer (called by VMs on open)."""
        grants = self._coordinator.register_consumer(consumer_id)
        self._consumers.add(consumer_id)
        for grant in grants:
            self.worker(WORKER_NAME).send_grant(grant)

    def remove_consumer(self, consumer_id: str) -> None:
        """Unregister a consumer, force-acking any in-flight slots (called by VMs on close)."""
        self._consumers.discard(consumer_id)
        grants = self._coordinator.unregister_consumer(consumer_id)
        for grant in grants:
            self.worker(WORKER_NAME).send_grant(grant)

    # ------------------------------------------------------------------
    # Slot ack (forwarded from consumers)
    # ------------------------------------------------------------------

    def ack_slot(self, slot: int, item_id: int, consumer_id: str) -> None:
        self.worker(WORKER_NAME).ack_slot(slot=slot, item_id=item_id, consumer_id=consumer_id)

    # ------------------------------------------------------------------
    # Internal routing
    # ------------------------------------------------------------------

    def _on_item_available(self, msg: ItemAvailable) -> None:
        if msg.buffer_id != WORKER_NAME:
            return
        if msg.consumer_id not in self._consumers:
            return
        self._bus.publish(SpectrumAvailable(
            slot=msg.slot,
            item_id=msg.item_id,
            timestamp_ns=msg.timestamp_ns,
            consumer_id=msg.consumer_id,
        ))
