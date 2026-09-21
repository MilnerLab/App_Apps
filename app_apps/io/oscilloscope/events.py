from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OscilloscopeWorkerStateChanged:
    """Published on every OscilloscopeWorkerHandle status transition (no payload).

    Subscribers read ``handle.state`` for the current ``WorkerStatus``."""


@dataclass(frozen=True)
class OscilloscopeConfigChanged:
    """Published when the user applies new oscilloscope settings."""


@dataclass(frozen=True)
class OscilloscopeTimebaseChanged:
    """Published when the worker reports a new sample interval.

    Subscribers read ``handle.dt_s``. The instrument's horizontal scale is a knob, so
    this can fire at any time while the stream is running -- not only at start.
    """


@dataclass(frozen=True)
class TraceAvailable:
    """
    Published when a new trace slot is ready.

    Broadcast to all registered consumers -- each reads the same slot from shared memory
    and acks with its own consumer_id. The slot is not reused until every one of them
    has acked, so a consumer that goes quiet stalls the stream for all of them.
    """
    slot: int
    item_id: int
    timestamp_ns: int


@dataclass(frozen=True)
class TraceAck:
    """Published by a consumer after it finishes reading a trace slot."""
    slot: int
    item_id: int
    consumer_id: str
