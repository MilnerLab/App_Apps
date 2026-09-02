from __future__ import annotations

import logging
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

log = logging.getLogger(__name__)


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
        """Apply the config, and start only once it has actually been applied.

        These were fired back to back, and that was a race the operator lost every time they
        edited a setting before starting. StartWorker is handled on the subprocess POLL
        thread, while SetSpectrometerConfig is dispatched onto the worker thread -- so
        despite going down the pipe in order, _start() would frequently run first, against
        whatever config the worker was still holding: the previous one, or on a fresh
        process, None. Chaining start off the reply makes the order real rather than likely.

        A rejected setting therefore no longer half-starts the worker: the error surfaces
        from the config request and StartWorker is never sent.
        """
        self._request(
            SetSpectrometerConfig(config=self._config),
            lambda _reply: super(SpectrometerWorkerHandle, self).start(),
            on_error=self._on_start_config_error,
        )

    def _on_start_config_error(self, err) -> None:
        # Deliberately NOT followed by a start. The device would come up on settings the
        # operator did not ask for, which is worse than not coming up: the spectra would
        # look plausible and be taken at the wrong exposure.
        log.error("Spectrometer: not starting -- the configuration was rejected: %s",
                  getattr(err, "error", err))
        self._on_error(err)

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
