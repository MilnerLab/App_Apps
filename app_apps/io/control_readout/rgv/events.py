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
    """Published whenever the plate starts, changes rate or stops free-running.

    ``error`` is empty on every ordinary transition. It is filled in only when a spin the
    handler had already optimistically announced was *rejected* by the controller, so the
    panel can say why the plate it was told about is not turning.
    """
    spinning: bool
    velocity_deg_s: float
    error: str = ""
