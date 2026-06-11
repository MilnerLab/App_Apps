from __future__ import annotations

from typing import ClassVar

from base_core.framework.app.app_message import AppMessage, MessageLevel
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.subprocess.subprocess_service import SubprocessService
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from base_core.framework.subprocess.shared_memory.buffer_output import BufferOutput
from base_core.framework.subprocess.shared_memory.shared_buffer_coordinator import SharedBufferCoordinator
from base_core.framework.subprocess.worker_handle import WorkerHandle
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer

from app_apps.io.spectrometer.events import SpectrumAvailable, SpectrumAck


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

        # Handle must exist before BufferOutput so send_grant is available.
        # BufferOutput must exist before with_output so item_notifier can be wired.
        handle = WorkerHandle(service=self, name=WORKER_NAME, bus=self._internal_bus)
        self._output: BufferOutput[SpectrumAvailable, SpectrumAck] = BufferOutput(
            coordinator=coordinator,
            send_grant=handle.send_grant,
            bus=bus,
            available_cls=SpectrumAvailable,
            ack_cls=SpectrumAck,
            buffer_id=WORKER_NAME,
        )
        handle.with_output(buffer, self._output)
        self._register_handle(WORKER_NAME, handle)

    @property
    def output(self) -> BufferOutput:
        return self._output

    def start(self) -> None:
        super().start()
        self._output.start()
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
        self._output.stop()
        super().stop()
