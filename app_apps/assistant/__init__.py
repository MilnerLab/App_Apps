"""LLM control layer — a physics-informed assistant over the linear routine registry.

A small Claude model maps natural-language commands to the *closed set* of registered routines
(tool-use built from `RoutineSpec`), validates parameters, and either auto-runs `safe` routines
or asks a human to confirm. Off by default; runtime kill switch. See
`docs/routine_authoring_plan.md` (LLM roadmap) and the approved plan.

This module is built bottom-up; L1 = `tools` + `validation`, L2 = `assistant` core + `events`.
"""
from app_apps.assistant.assistant import Assistant
from app_apps.assistant.client import ClaudeClient, LLMClient, ToolCall
from app_apps.assistant.models import (
    AssistantResult,
    CodeProposal,
    Proposal,
    ResultKind,
)
from app_apps.assistant.planner import AcceptResult, accept_routine
from app_apps.assistant.prompt import build_system_prompt
from app_apps.assistant.validation import ParamValidationError, validate_params

__all__ = [
    "Assistant",
    "AssistantResult",
    "ResultKind",
    "Proposal",
    "CodeProposal",
    "LLMClient",
    "ClaudeClient",
    "ToolCall",
    "build_system_prompt",
    "accept_routine",
    "AcceptResult",
    "ParamValidationError",
    "validate_params",
]
