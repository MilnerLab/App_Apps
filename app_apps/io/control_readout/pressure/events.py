from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PressureAvailable:
    slot: int
    item_id: int
    timestamp_ns: int


@dataclass(frozen=True)
class PressureAck:
    slot: int
    item_id: int
    consumer_id: str
