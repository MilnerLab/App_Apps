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
from base_core.framework.subprocess.shared_memory.buffer_output import BufferOutput
from base_core.framework.subprocess.worker_handle import WorkerHandle
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer

from app_apps.io.spectrometer.events import SpectrumAvailable


WORKER_NAME = "spectrometer"


class SpectrometerService(SubprocessService):
    """
    Main-process handle to the SPM-002 spectrometer subprocess.

    UI consumers register via the BufferOutput returned by .output.  The
    coordinator is an internal detail; it is not exposed in the DI container.
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
        self._item_available_sub = None
        self._ui_consumers: set[str] = set()
        self._output = BufferOutput(
            coordinator=coordinator,
            send_grant=self.worker(WORKER_NAME).send_grant,
            on_register=self._ui_consumers.add,
            on_unregister=self._ui_consumers.discard,
        )

    @property
    def output(self) -> BufferOutput:
        return self._output

    def start(self) -> None:
        super().start()
        self._item_available_sub = self._bus.subscribe(ItemAvailable, self._on_item_available)
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
        if self._item_available_sub is not None:
            self._item_available_sub()
            self._item_available_sub = None
        super().stop()

    # ------------------------------------------------------------------
    # Internal routing
    # ------------------------------------------------------------------

    def _on_item_available(self, msg: ItemAvailable) -> None:
        if msg.buffer_id != WORKER_NAME:
            return
        if msg.consumer_id not in self._ui_consumers:
            return
        self._bus.publish(SpectrumAvailable(
            slot=msg.slot,
            item_id=msg.item_id,
            timestamp_ns=msg.timestamp_ns,
            consumer_id=msg.consumer_id,
        ))
