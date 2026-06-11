from __future__ import annotations

from enum import Enum

from base_core.framework.serialization.serde import Primitive, PrimitiveSerde


class ControlMode(str, Enum):
    PHASE_TRACKING = "phase_tracking"
    ENVELOPE = "envelope"

    def to_primitive(self) -> Primitive:
        return self.value

    @classmethod
    def from_primitive(cls, v: Primitive) -> "ControlMode":
        return cls(v)


PrimitiveSerde.register(ControlMode)
