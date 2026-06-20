from __future__ import annotations

from dataclasses import dataclass

from base_core.math.models import Angle


@dataclass(frozen=True)
class RequestRotate:
    angle: Angle
    sign: int

@dataclass(frozen=True)
class NewELL14Angle:
    angle: Angle

@dataclass(frozen=True)
class ELL14RotatorHomed:
    pass

@dataclass(frozen=True)
class ELL14WorkerStateChanged:
    pass
