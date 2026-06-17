from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.framework.shm.writer_worker_handle import WriterWorkerHandle
from base_core.ipc.message import OKReply
from spm_002.config import SpectrometerConfig
from spm_002.messages import SetSpectrometerConfig

from app_apps.io.spectrometer.buffer import SpectrumBuffer, SpectrumMemorySpec
from app_apps.io.spectrometer.events import SpectrumAvailable, SpectrumAck

class SpectrometerWorkerHandle(WriterWorkerHandle[SpectrumBuffer, SpectrumAvailable, SpectrumAck]):
    """
    Main-process handle to SpectrometerWorker.

    Owns the SpectrumBuffer shared memory and the SlotCoordinator. Exposes typed
    wrappers for all commands the main process can send to the spectrometer worker.
    Start/stop/reset and slot coordination are inherited from WriterWorkerHandle.

    Usage (from module or UI layer):
        handle.start()                      # begins acquisition
        handle.stop()                       # pauses acquisition
        handle.set_config(new_config)       # updates hardware settings live
        handle.register_consumer(id)        # from read-only consumers (e.g. PhaseControlService)
    """

    WORKER_ID = "spectrometer"

    def __init__(self, bus: EventBus, spec: SpectrumMemorySpec) -> None:
        super().__init__(
            worker_id=self.WORKER_ID,
            bus=bus,
            buffer_cls=SpectrumBuffer,
            spec=spec,
            make_available=lambda slot, item_id, ts: SpectrumAvailable(
                slot=slot, item_id=item_id, timestamp_ns=ts
            ),
            ack_type=SpectrumAck,
        )

    def set_config(self, config: SpectrometerConfig) -> None:
        """Send a new SpectrometerConfig to the subprocess and apply it to the hardware."""
        self._request(
            SetSpectrometerConfig(config=config),
            self._on_set_config_reply,
        )

    def _on_set_config_reply(self, reply: OKReply) -> None:
        pass
