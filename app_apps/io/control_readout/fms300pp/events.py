from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestMoveFms300pp:
    position: float


@dataclass(frozen=True)
class NewFms300ppPosition:
    position: float


@dataclass(frozen=True)
class Fms300ppWorkerStateChanged:
    pass
