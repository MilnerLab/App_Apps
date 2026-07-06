from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestMoveMfacc:
    position: float


@dataclass(frozen=True)
class NewMfaccPosition:
    position: float


@dataclass(frozen=True)
class MfaccWorkerStateChanged:
    pass
