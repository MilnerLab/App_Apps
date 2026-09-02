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


@dataclass(frozen=True)
class RgvWorkerStateChanged:
    pass


@dataclass(frozen=True)
class RequestSpinRGV:
    """Ask for continuous rotation at ``velocity_deg_s``. Sign sets the direction."""
    velocity_deg_s: float


@dataclass(frozen=True)
class RequestStopSpinRGV:
    pass


@dataclass(frozen=True)
class RgvSpinStateChanged:
    """Published whenever the plate starts, changes rate or stops free-running."""
    spinning: bool
    velocity_deg_s: float
