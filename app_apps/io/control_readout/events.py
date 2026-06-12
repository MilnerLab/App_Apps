from __future__ import annotations

from dataclasses import dataclass

from base_core.math.models import Angle


@dataclass(frozen=True)
class RequestRotate:
    angle: Angle
    sign: int


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
