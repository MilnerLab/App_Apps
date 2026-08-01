from __future__ import annotations

from typing import Callable

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import ErrorReply, OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from oscilloscope.config import ScopeConfig
from oscilloscope.messages import AcquirePoint, AcquirePointReply, SetScopeConfig

from app_apps.io.oscilloscope.events import OscilloscopeWorkerStateChanged


class OscilloscopeWorkerHandle(BaseWorkerHandle):
    """Main-process handle to the OscilloscopeWorker.

    Plain ``BaseWorkerHandle`` — no shared memory, no ``SlotCoordinator`` (B14). The
    worker replies to ``AcquirePoint`` with per-trace scalars, so nothing here reads a
    trace buffer. ``acquire_point`` follows the same reply-correlated blocking pattern
    as the stage handles' ``move_to``: the caller passes ``on_reply``/``on_error`` and
    a routine turns them into a blocking call.
    """

    WORKER_ID = "oscilloscope"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=OscilloscopeWorkerStateChanged)

    def set_config(self, config: ScopeConfig) -> None:
        """Apply an acquisition config. Send **before** ``start()`` (B5)."""
        self._request(SetScopeConfig(config=config), self._on_reply)

    def acquire_point(
        self,
        *,
        n_traces: int,
        channel: int,
        probe_mm: float,
        discard: int,
        on_reply: Callable[[list[float], list[int]], None],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        """Acquire ``n_traces`` freshness-gated traces and get back the reduced scalars.

        ``on_reply(values, counts)`` receives the per-trace positive-means and their
        positive-sample counts (D3 step 1); the caller does the across-trace average.
        Callbacks run on the IPC reader thread — keep them short.
        """
        self._request(
            AcquirePoint(n_traces=n_traces, channel=channel, discard=discard, probe_mm=probe_mm),
            lambda reply: on_reply(list(reply.values), list(reply.counts)),
            (lambda err: on_error(err.error)) if on_error is not None else None,
        )

    def _on_reply(self, reply: OKReply) -> None:
        pass
