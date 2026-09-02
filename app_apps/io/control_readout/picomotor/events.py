"""App-side events for the mirror picomotors.

``steps`` everywhere is the controller's open-loop counter, never a calibrated
position — the 8742 has no encoder. The naming is load-bearing: a field called
``position`` on this device would invite exactly the mistake the hardware cannot
support.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RequestStepPicomotor:
    """Relative move — the primary control."""

    axis: int
    steps: int


@dataclass(frozen=True)
class RequestStepPicomotorTo:
    """Absolute move on the step counter. A convenience, not a calibrated position."""

    axis: int
    steps: int


@dataclass(frozen=True)
class RequestZeroPicomotor:
    """Re-reference an axis counter to zero. Moves nothing."""

    axis: int


@dataclass(frozen=True)
class RequestPicomotorSteps:
    """Ask for the current counters without moving anything."""

    axes: tuple[int, ...] = ()


@dataclass(frozen=True)
class PicomotorStepsChanged:
    """Latest known counters, ``{axis: total_steps}``.

    Emitted after any move, any zero, and any explicit query.
    """

    steps: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PicomotorWorkerStateChanged:
    pass
