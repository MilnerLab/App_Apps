from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestMove:
    """Request an absolute move of one ESP301 axis."""

    axis: int
    position: float
