from __future__ import annotations

import logging
from multiprocessing.shared_memory import SharedMemory

from base_core.framework.events.event_bus import EventBus
from base_core.framework.shm.writer_worker_handle import WriterWorkerHandle
from base_core.ipc.connection_mode import ConnectionMode
from base_core.ipc.device_worker_handle import DeviceHandleMixin
from base_core.ipc.message import OKReply
from oscilloscope.buffer import ScopeBuffer, ScopeMemorySpec
from oscilloscope.config import ScopeConfig
from oscilloscope.messages import ScopeTimebase, SetScopeConfig

from app_apps.io.oscilloscope.events import (
    OscilloscopeConfigChanged,
    OscilloscopeTimebaseChanged,
    OscilloscopeWorkerStateChanged,
    TraceAck,
    TraceAvailable,
)

log = logging.getLogger(__name__)


class OscilloscopeWorkerHandle(
    DeviceHandleMixin,
    WriterWorkerHandle[ScopeBuffer, TraceAvailable, TraceAck],
):
    """
    Main-process handle to OscilloscopeWorker.

    Owns the ScopeBuffer shared memory and the SlotCoordinator. The worker streams every
    trace into a slot and sends only a slot number down the pipe; consumers read the
    frame here and ack it.

    Usage:
        handle.start()                      # applies the config, then begins streaming
        handle.register_consumer(id)        # from read-only consumers (plot, routine)
        buf = handle.buffer
        row = buf.trace(slot, handle.config.channels, handle.config.n_samples)

    Every registered consumer must ack every frame. The coordinator holds the slot until
    the last ack arrives, so a consumer that stops acking stops the stream for all of
    them -- the XCORR scan included.
    """

    WORKER_ID = "oscilloscope"

    def __init__(self, bus: EventBus, spec: ScopeMemorySpec, config: ScopeConfig) -> None:
        super().__init__(
            worker_id=self.WORKER_ID,
            bus=bus,
            buffer_cls=ScopeBuffer,
            spec=spec,
            make_available=lambda slot, item_id, ts: TraceAvailable(
                slot=slot, item_id=item_id, timestamp_ns=ts
            ),
            ack_type=TraceAck,
            state_event=OscilloscopeWorkerStateChanged,
        )
        self._config = config
        self._dt_s = 0.0

    @property
    def config(self) -> ScopeConfig:
        """The settings in force. A consumer needs the record shape to slice a frame out
        of its slot, since the slot itself is sized to the instrument's largest record."""
        return self._config

    @property
    def buffer(self) -> ScopeBuffer:
        assert self._writer_buffer is not None, "buffer not yet created (service not started)"
        return self._writer_buffer

    @property
    def dt_s(self) -> float:
        """Sample interval in seconds as last reported by the instrument, 0.0 before the
        first trace. A display with no time base should label its axis in samples."""
        return self._dt_s

    def trace(self, slot: int):
        """The frame in ``slot``, sliced to the configured record and copied out."""
        return self.buffer.trace(slot, self._config.channels, self._config.n_samples)

    def _bind(self, connector, service_bus) -> None:  # type: ignore[override]
        # Unlink any stale segment left by a previous crash (POSIX shm persists across processes)
        try:
            SharedMemory(name=self._spec.name, create=False).unlink()
        except FileNotFoundError:
            pass
        super()._bind(connector, service_bus)

    def subscribe(self) -> None:
        self._subscribe(OscilloscopeConfigChanged, self._on_config_changed)
        # Over the pipe, so the service bus -- not the application bus.
        self._subscribe_service(ScopeTimebase, self._on_timebase)

    def start(self, mode: ConnectionMode | None = None):
        """Apply the config, and start only once it has actually been applied.

        The worker builds its driver from the config in _start() and does not re-open on a
        later change, so the config has to land first. Sent back to back it would not
        reliably: StartWorker is handled on the subprocess poll thread while SetScopeConfig
        is dispatched onto the worker thread, so despite going down the pipe in order
        _start() often runs first, against whatever config the worker still holds -- the
        previous one, or on a fresh process the defaults. Chaining start off the reply
        makes the order real rather than likely.

        A rejected record length therefore no longer half-starts the worker: the error
        surfaces from the config request and StartWorker is never sent.
        """
        self._request(
            SetScopeConfig(config=self._config),
            lambda _reply: super(OscilloscopeWorkerHandle, self).start(mode),
            on_error=self._on_start_config_error,
        )

    def _on_start_config_error(self, err) -> None:
        # Deliberately NOT followed by a start. A record the buffer cannot hold would fail
        # on every single write, which reads as a scope that connected and then went silent.
        log.error("Oscilloscope: not starting -- the configuration was rejected: %s",
                  getattr(err, "error", err))
        self._on_error(err)

    def set_config(self) -> None:
        """Send a new ScopeConfig to the subprocess.

        Record length and channel count only reach the instrument at the next start: the
        driver writes RECOrdlength in apply_config(), which runs when the device is opened.
        """
        self._request(SetScopeConfig(config=self._config), self._on_set_config_reply)

    def _on_config_changed(self, _: OscilloscopeConfigChanged) -> None:
        self.set_config()

    def _on_set_config_reply(self, reply: OKReply) -> None:
        pass

    def _on_timebase(self, msg: ScopeTimebase) -> None:
        self._dt_s = msg.dt_s
        self._bus.publish(OscilloscopeTimebaseChanged())
