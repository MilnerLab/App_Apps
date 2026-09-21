from __future__ import annotations

import logging
from multiprocessing.shared_memory import SharedMemory
from typing import Generic, TypeVar

from base_core.framework.events.event_bus import EventBus
from base_core.framework.shm.writer_worker_handle import WriterWorkerHandle
from base_core.ipc.message import OKReply
from camera.base.buffer import CameraBuffer, CameraMemorySpec
from camera.base.config import CameraConfig
from camera.base.messages import SetCameraConfig

log = logging.getLogger(__name__)

TAvailable = TypeVar("TAvailable")
TAck = TypeVar("TAck")


class CameraWorkerHandle(WriterWorkerHandle[CameraBuffer, TAvailable, TAck], Generic[TAvailable, TAck]):
    """
    Main-process handle to a CameraWorker.

    Generic across camera vendors: the domain event classes a given camera
    instance publishes (frame-available/ack/config-changed/state-changed) are
    injected via the constructor, so one handle class serves every camera --
    only the events.py module differs per vendor (e.g. app_apps/io/camera_vmi).

    Owns the CameraBuffer shared memory and the SlotCoordinator. Exposes typed
    wrappers for all commands the main process can send to the camera worker.
    Start/stop/reset and slot coordination are inherited from WriterWorkerHandle.

    Usage (from a camera module):
        handle.start()                      # applies config, then begins acquisition
        handle.pause()                      # pauses acquisition
        handle.set_config()                 # pushes self.config live to the hardware
        handle.register_consumer(id)        # from read-only consumers
    """

    def __init__(
        self,
        worker_id: str,
        bus: EventBus,
        spec: CameraMemorySpec,
        config: CameraConfig,
        frame_available_cls: type[TAvailable],
        frame_ack_cls: type[TAck],
        config_changed_cls: type,
        state_changed_cls: type,
    ) -> None:
        super().__init__(
            worker_id=worker_id,
            bus=bus,
            buffer_cls=CameraBuffer,
            spec=spec,
            make_available=lambda slot, item_id, ts: frame_available_cls(
                slot=slot, item_id=item_id, timestamp_ns=ts
            ),
            ack_type=frame_ack_cls,
            state_event=state_changed_cls,
        )
        self._config = config
        self._config_changed_cls = config_changed_cls

    @property
    def config(self) -> CameraConfig:
        """The settings in force. Read-only access for consumers."""
        return self._config

    @property
    def buffer(self) -> CameraBuffer:
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
        self._subscribe(self._config_changed_cls, self._on_config_changed)

    def start(self):
        """Apply the config, and start only once it has actually been applied.

        StartWorker is handled on the subprocess POLL thread, while
        SetCameraConfig is dispatched onto the worker thread -- so despite
        going down the pipe in order, _start() could run first, against
        whatever config the worker was still holding (the previous one, or on
        a fresh process, None). Chaining start off the reply makes the order
        real rather than likely -- the same race spectrometer's handle guards
        against.
        """
        self._request(
            SetCameraConfig(config=self._config),
            lambda _reply: super(CameraWorkerHandle, self).start(),
            on_error=self._on_start_config_error,
        )

    def _on_start_config_error(self, err) -> None:
        # Deliberately NOT followed by a start -- the device would come up on
        # settings the operator did not ask for.
        log.error("Camera %r: not starting -- the configuration was rejected: %s",
                  self._worker_id, getattr(err, "error", err))
        self._on_error(err)

    def set_config(self) -> None:
        """Send the current CameraConfig to the subprocess and apply it to the hardware."""
        self._request(
            SetCameraConfig(config=self._config),
            self._on_set_config_reply,
        )

    def _on_config_changed(self, _: object) -> None:
        self.set_config()

    def _on_set_config_reply(self, reply: OKReply) -> None:
        pass
