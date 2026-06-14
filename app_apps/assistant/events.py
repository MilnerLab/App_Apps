"""Assistant lifecycle events (bus payloads, primitives only) for UI/observers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CommandReceived:
    command: str


@dataclass(frozen=True)
class ProposalReady:
    """A routine that moves hardware awaits human confirmation."""

    proposal_id: str
    routine: str
    params: dict[str, Any]
    summary: str


@dataclass(frozen=True)
class RoutineAutoLaunched:
    """A `safe` routine was launched without confirmation."""

    routine: str
    params: dict[str, Any]


@dataclass(frozen=True)
class CodeProposed:
    """The planner produced candidate routine code (not run)."""

    name: str
    goal: str


@dataclass(frozen=True)
class AssistantError:
    command: str
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AssistantEnabled:
    pass


@dataclass(frozen=True)
class AssistantDisabled:
    pass
