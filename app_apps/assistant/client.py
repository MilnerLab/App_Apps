"""The LLM client seam.

A minimal interface so the assistant core is testable with a fake (no network) and the real
Claude implementation (L3) plugs in behind it. `propose` returns the single tool the model
wants to call for a command (or None if it didn't pick one); `feedback` carries a validation
error back for one self-correction attempt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class ToolCall:
    """The model's chosen tool and arguments."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    def propose(
        self,
        command: str,
        tools: list[dict[str, Any]],
        system: str,
        *,
        feedback: Optional[str] = None,
    ) -> Optional[ToolCall]:
        """Return the tool the model wants to call for `command`, or None."""
        ...
