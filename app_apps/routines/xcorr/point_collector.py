"""``XcorrPointCollector`` — reduce the free-running scope stream into measurement points.

The scope is not driven by the scan. Like the spectrometer, it free-runs in its own
subprocess, pushing every trace into a two-slot shared-memory buffer. This class registers
as a consumer of that stream and turns a burst of traces into the scalars one probe point
is made of.

It used to be the worker's job: the main process sent ``AcquirePoint``, the worker drove
the instrument itself and replied with the reduced scalars, because putting 2500 samples
per trace through a JSON pipe to average them here would have been absurd. Shared memory
removes that reason — the frames are already in this process at no cost — and removes a
second problem with it. A continuous producer and an on-demand request are two threads
reaching for one VISA session; now exactly one thread touches the instrument and the
reduction happens where the result is used.

**The reduction, unchanged.** For each trace, take the configured channel's row, keep the
positive samples, and average them (D3 step 1). The across-trace mean and spread (D3
step 2) stay in the routine, which is where the point's uncertainty is recorded.

**The freshness gate.** A trace captured while a stage was still moving biases the point
toward where the probe used to be, which reads as a real feature in the interferogram.
``TraceAvailable.timestamp_ns`` marks the moment the capture *began* (see
``oscilloscope.models.ScopeTrace``), so a trace is admitted only when it began after the
move returned. That is a strictly sharper test than the trace-counting discard it
replaces, which could only guess how many stale records the instrument was holding;
``XcorrConfig.in_flight_discard`` survives as an extra margin on top of it.

**Acks are on the critical path of an unrelated subsystem.** ``SlotCoordinator`` promotes
the next trace only once every registered consumer has acked, and the handler runs
synchronously on the IPC reader thread. So :meth:`_on_trace` copies, reduces and acks,
and acks in a ``finally`` whatever else happened: every frame is acked, whether or not a
collection is in progress.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from base_core.framework.events.event_bus import EventBus

from app_apps.io.oscilloscope.events import TraceAck, TraceAvailable

if TYPE_CHECKING:
    from app_apps.io.oscilloscope.oscilloscope_worker_handler import OscilloscopeWorkerHandle

log = logging.getLogger(__name__)


@dataclass
class _Collection:
    """One point's worth of collecting, armed by the routine thread and filled by the
    IPC reader thread."""

    channel: int
    gate_ns: int
    skip: int
    n_traces: int
    values: list[float] = field(default_factory=list)
    counts: list[int] = field(default_factory=list)
    done: threading.Event = field(default_factory=threading.Event)


class XcorrPointCollector:
    """Consume the trace stream for the duration of one XCORR run.

    Free of Qt, like the spectrum recorder: the routine half of XCORR must stay importable
    from the headless harness, which never builds a window.
    """

    CONSUMER_ID = "xcorr_point"

    def __init__(self, bus: EventBus, handle: "OscilloscopeWorkerHandle") -> None:
        self._bus = bus
        self._handle = handle
        self._unsub = None
        # Guards the armed collection. Written by the routine thread, read by the IPC
        # reader thread on every frame.
        self._lock = threading.Lock()
        self._active: _Collection | None = None
        #: Frames seen while nothing was armed, or rejected by the gate. Logged at close
        #: because a run where most traces were gated out is a run to be suspicious of.
        self._n_gated_out = 0
        self._n_idle = 0

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Register as a consumer and start acking.

        Subscribe before registering: registering is what makes the coordinator wait on
        our ack, so there must be no window where we are pending but not listening.
        """
        self._unsub = self._bus.subscribe(TraceAvailable, self._on_trace)
        self._handle.register_consumer(self.CONSUMER_ID)
        log.info("XCORR point collector started")

    def close(self) -> None:
        """Unregister, then stop listening.

        Ordering matters: unregister *first* so the coordinator stops waiting on our ack,
        then unsubscribe. The other way round leaves a promoted slot pending on a consumer
        that no longer listens, stalling the stream until the handle is torn down.
        """
        self._handle.unregister_consumer(self.CONSUMER_ID)
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        with self._lock:
            active, self._active = self._active, None
        if active is not None:
            active.done.set()  # release a caller that is still waiting
        log.info(
            "XCORR point collector stopped: %d frames gated out, %d seen while idle",
            self._n_gated_out, self._n_idle,
        )

    # -- collecting (routine thread) --------------------------------------

    def collect(
        self,
        *,
        n_traces: int,
        channel: int,
        gate_ns: int,
        skip: int,
        timeout_s: float,
    ) -> tuple[list[float], list[int]]:
        """Block until ``n_traces`` admissible traces have been reduced.

        ``gate_ns`` is the wall clock at which the stages stopped moving; traces whose
        capture began before it are ignored. ``skip`` drops that many admissible traces
        first, as a margin on top of the gate. Returns the per-trace positive-means and
        the sample count behind each -- short lists if the timeout ran out first, which
        the caller reports as a failed point.
        """
        channels = self._handle.config.channels
        if channel < 1 or channel > channels:
            raise ValueError(
                f"channel {channel} is not in this trace: the scope is configured for "
                f"{channels} channel(s)")

        collection = _Collection(
            channel=channel,
            gate_ns=int(gate_ns),
            skip=max(int(skip), 0),
            n_traces=max(int(n_traces), 1),
        )
        with self._lock:
            self._active = collection
        try:
            collection.done.wait(timeout_s)
        finally:
            with self._lock:
                self._active = None
        return list(collection.values), list(collection.counts)

    # -- consumer (IPC reader thread — must stay short) --------------------

    def _on_trace(self, event: TraceAvailable) -> None:
        try:
            with self._lock:
                active = self._active
            if active is None or active.done.is_set():
                self._n_idle += 1
                return
            if event.timestamp_ns < active.gate_ns:
                # Capture began before the stages settled: it belongs to the old position.
                self._n_gated_out += 1
                return
            if active.skip > 0:
                active.skip -= 1
                return

            # Copy out of shared memory: the slot is reused the moment we ack.
            frame = self._handle.trace(event.slot)
            row = frame[active.channel - 1]
            positive = row[row > 0.0]                                      # D3 step 1
            active.values.append(float(positive.mean()) if positive.size else 0.0)
            active.counts.append(int(positive.size))
            if len(active.values) >= active.n_traces:
                active.done.set()
        except Exception:
            # Never let a read error escape: this runs on the IPC reader thread, and an
            # exception here would skip the ack below and stall the whole stream.
            log.exception("XCORR point collector: read failed for slot %d", event.slot)
        finally:
            self._bus.publish(TraceAck(
                slot=event.slot, item_id=event.item_id, consumer_id=self.CONSUMER_ID
            ))
