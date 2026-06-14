"""LLM control layer — a physics-informed assistant over the linear routine registry.

A small Claude model maps natural-language commands to the *closed set* of registered routines
(tool-use built from `RoutineSpec`), validates parameters, and either auto-runs `safe` routines
or asks a human to confirm. Off by default; runtime kill switch. See
`docs/routine_authoring_plan.md` (LLM roadmap) and the approved plan.

This module is built bottom-up; L1 = `tools` (schema builder) + `validation`.
"""
from app_apps.assistant.validation import ParamValidationError, validate_params

__all__ = ["ParamValidationError", "validate_params"]
