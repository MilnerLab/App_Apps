from __future__ import annotations

from dataclasses import dataclass

from base_core.math.models import Angle


@dataclass(frozen=True)
class RequestRotateRGV:
    angle: Angle
    
@dataclass(frozen=True)
class RequestCurrentRGVAngle:
    pass

@dataclass(frozen=True)
class NewRGVAngle:
    angle: Angle
    
