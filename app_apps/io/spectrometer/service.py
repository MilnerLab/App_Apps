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
from spm_002.spectrometer_worker import SpectrometerWorker

from app_apps.io.spectrometer.events import SpectrumAvailable, SpectrumAck


class SpectrometerService(SubprocessService):
    """
    Main-process handle to the SPM-002 spectrometer subprocess.

    UI consumers register via the BufferOutput returned by .output.
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

        send_grant = self.make_send_grant(SpectrometerWorker.name, SpectrometerWorker.name)
        self._output: BufferOutput[SpectrumAvailable, SpectrumAck] = BufferOutput(
            coordinator=coordinator,
            send_grant=send_grant,
            bus=bus,
            available_cls=SpectrumAvailable,
            ack_cls=SpectrumAck,
            buffer_id=SpectrometerWorker.name,
        )
        self.set_write_buffer(SpectrometerWorker.name, SpectrometerWorker.name, buffer, self._output)
        self._register_handle(SpectrometerWorker.name, WorkerHandle(service=self, name=SpectrometerWorker.name))

    @property
    def output(self) -> BufferOutput:
        return self._output

    def start(self) -> None:
        super().start()
        self._output.start()
        self._publish_status(True)

    def start_spectrometer(self) -> None:
        self.worker(SpectrometerWorker.name).start_async(
            post_start=self._send_initial_grants,
            key="spectrometer.worker.start",
            on_error=lambda exc: self._bus.publish(
                AppMessage(f"Spectrometer failed to start: {exc}", MessageLevel.ERROR)
            ),
        )

    def stop_spectrometer(self) -> None:
        self.worker(SpectrometerWorker.name).stop()

    def stop(self) -> None:
        self._publish_status(False)
        self._output.stop()
        super().stop()
