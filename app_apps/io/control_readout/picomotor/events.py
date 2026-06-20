from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestStepPicomotor:
    axis: int
    steps: int
