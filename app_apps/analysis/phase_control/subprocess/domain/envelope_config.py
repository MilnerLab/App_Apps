from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from base_core.framework.serialization.serde import Primitive, PrimitiveSerde
from base_core.math.enums import AngleUnit
from base_core.math.models import Angle, Range
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Length


class EnvelopeMode(str, Enum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


@dataclass
class EnvelopeConfig(PrimitiveSerde):
    wavelength_range: Range[Length] = Range(Length(796, Prefix.NANO), Length(810, Prefix.NANO))
    step_angle: Angle = Angle(1, AngleUnit.DEG)
    smooth_window: int = 5
    mode: EnvelopeMode = EnvelopeMode.MAXIMIZE

    def to_primitive(self) -> Primitive:
        return {
            "wavelength_range": {
                "min": self.wavelength_range.min.to_primitive(),
                "max": self.wavelength_range.max.to_primitive(),
            },
            "step_angle": self.step_angle.to_primitive(),
            "smooth_window": self.smooth_window,
            "mode": self.mode.value,
        }

    @classmethod
    def from_primitive(cls, v: Primitive) -> "EnvelopeConfig":
        wl = v["wavelength_range"]
        return cls(
            wavelength_range=Range(
                Length.from_primitive(wl["min"]),
                Length.from_primitive(wl["max"]),
            ),
            step_angle=Angle.from_primitive(v["step_angle"]),
            smooth_window=int(v["smooth_window"]),
            mode=EnvelopeMode(v["mode"]),
        )
