from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TraceAvailable:
    """Published by OscilloscopeService when a new scope-trace slot is ready."""

    slot: int
    item_id: int
    timestamp_ns: int


@dataclass(frozen=True)
class TraceAck:
    """Published by a consumer after it finishes reading a trace slot."""

    slot: int
    item_id: int
    consumer_id: str
