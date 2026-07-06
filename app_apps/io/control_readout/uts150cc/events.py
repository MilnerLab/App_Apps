from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestMoveUts150cc:
    position: float


@dataclass(frozen=True)
class NewUts150ccPosition:
    position: float


@dataclass(frozen=True)
class Uts150ccWorkerStateChanged:
    pass
