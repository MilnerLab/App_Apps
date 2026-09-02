from __future__ import annotations

from multiprocessing.shared_memory import SharedMemory

from base_core.framework.events.event_bus import EventBus
from base_core.framework.shm.writer_worker_handle import WriterWorkerHandle
from base_core.ipc.message import OKReply
from spm_002.config import SpectrometerConfig
from spm_002.messages import SetSpectrometerConfig

from spm_002.buffer import SpectrumBuffer, SpectrumMemorySpec
from app_apps.io.spectrometer.events import (
    SpectrumAvailable,
    SpectrumAck,
    SpectrometerConfigChanged,
    SpectrometerWorkerStateChanged,
)

class SpectrometerWorkerHandle(WriterWorkerHandle[SpectrumBuffer, SpectrumAvailable, SpectrumAck]):
    """
    Main-process handle to SpectrometerWorker.

    Owns the SpectrumBuffer shared memory and the SlotCoordinator. Exposes typed
    wrappers for all commands the main process can send to the spectrometer worker.
    Start/stop/reset and slot coordination are inherited from WriterWorkerHandle.

    Usage (from module or UI layer):
        handle.start()                      # begins acquisition
        handle.pause()                      # pauses acquisition
        handle.set_config(new_config)       # updates hardware settings live
        handle.register_consumer(id)        # from read-only consumers (e.g. PhaseControlService)
    """

    WORKER_ID = "spectrometer"

    def __init__(self, bus: EventBus, spec: SpectrumMemorySpec, config: SpectrometerConfig) -> None:
        super().__init__(
            worker_id=self.WORKER_ID,
            bus=bus,
            buffer_cls=SpectrumBuffer,
            spec=spec,
            make_available=lambda slot, item_id, ts: SpectrumAvailable(
                slot=slot, item_id=item_id, timestamp_ns=ts
            ),
            ack_type=SpectrumAck,
            state_event=SpectrometerWorkerStateChanged,
        )
        self._config = config

    @property
    def config(self) -> SpectrometerConfig:
        """The settings in force. Read-only access for consumers that need to know the
        integration time — the XCORR spectrum recorder derives its motion gate from it."""
        return self._config

    @property
    def buffer(self) -> SpectrumBuffer:
        assert self._writer_buffer is not None, "buffer not yet created (service not started)"
        return self._writer_buffer

    def _bind(self, connector, service_bus) -> None:  # type: ignore[override]
        # Unlink any stale segment left by a previous crash (POSIX shm persists across processes)
        try:
            SharedMemory(name=self._spec.name, create=False).unlink()
        except FileNotFoundError:
            pass
        super()._bind(connector, service_bus)

    def subscribe(self) -> None:
        self._subscribe(SpectrometerConfigChanged, self._on_config_changed)
    
    def start(self):
        self.set_config()
        super().start()

    def set_config(self) -> None:
        """Send a new SpectrometerConfig to the subprocess and apply it to the hardware."""
        self._request(
            SetSpectrometerConfig(config=self._config),
            self._on_set_config_reply,
        )

    def _on_config_changed(self, _: SpectrometerConfigChanged) -> None:
        self.set_config()

    def _on_set_config_reply(self, reply: OKReply) -> None:
        pass
