"""Lifecycle events the runner publishes on the bus (for UI / observers / LLM tiers)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RoutineStarted:
    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutineCompleted:
    name: str


@dataclass(frozen=True)
class RoutineFailed:
    name: str
    error: str  # repr() of the exception


@dataclass(frozen=True)
class RoutineCancelledEvent:
    name: str
