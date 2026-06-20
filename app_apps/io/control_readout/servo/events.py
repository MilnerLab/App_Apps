from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestSetArmBlocked:
    arm: int
    blocked: bool
