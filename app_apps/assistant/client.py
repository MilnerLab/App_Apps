"""The LLM client seam.

A minimal interface so the assistant core is testable with a fake (no network) and the real
Claude implementation (L3) plugs in behind it. `propose` returns the single tool the model
wants to call for a command (or None if it didn't pick one); `feedback` carries a validation
error back for one self-correction attempt.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

#: Default model — a small/fast Claude (the "weak LLM on standby").
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


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


class ClaudeClient:
    """Real `LLMClient` backed by the Anthropic API (Claude Haiku, tool-use).

    `anthropic` is imported lazily so the package stays importable (and unit-testable with the
    fake client) without the SDK. Install it (`pip install anthropic`) and set
    `ANTHROPIC_API_KEY` to use this client.
    """

    def __init__(
        self, model: str = DEFAULT_MODEL, api_key: Optional[str] = None, max_tokens: int = 1024
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens

    def propose(
        self,
        command: str,
        tools: list[dict[str, Any]],
        system: str,
        *,
        feedback: Optional[str] = None,
    ) -> Optional[ToolCall]:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only in live use
            raise RuntimeError("the 'anthropic' package is required (pip install anthropic)") from exc

        api_key = self._api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

        client = anthropic.Anthropic(api_key=api_key)
        messages: list[dict[str, Any]] = [{"role": "user", "content": command}]
        if feedback:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"The previous arguments were invalid: {feedback}. "
                        "Call the same tool again with corrected arguments."
                    ),
                }
            )

        response = client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                return ToolCall(name=block.name, arguments=dict(block.input or {}))
        return None
