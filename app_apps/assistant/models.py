"""Return/proposal types for the assistant (what `handle()` / `confirm()` give back)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ResultKind(str, Enum):
    DISABLED = "disabled"  # assistant is off; nothing happened
    NO_ACTION = "no_action"  # model proposed no tool
    INFO = "info"  # answered a read-only query (list/status)
    LAUNCHED = "launched"  # a routine was started
    PROPOSAL = "proposal"  # a routine awaits human confirmation
    CODE_PROPOSAL = "code_proposal"  # planner produced candidate code (not run)
    ERROR = "error"  # invalid command / params
    BUSY = "busy"  # a routine is already running (single-flight)


@dataclass(frozen=True)
class Proposal:
    """A validated, ready-to-launch routine call awaiting (or already past) the safety gate."""

    id: str
    routine: str
    params: dict[str, Any]
    summary: str
    safe: bool


@dataclass(frozen=True)
class CodeProposal:
    """Candidate routine source from the T2 planner — never executed automatically."""

    name: str
    goal: str
    code: str


@dataclass(frozen=True)
class AssistantResult:
    kind: ResultKind
    message: str = ""
    proposal: Optional[Proposal] = None
    code_proposal: Optional[CodeProposal] = None
    data: Any = None
