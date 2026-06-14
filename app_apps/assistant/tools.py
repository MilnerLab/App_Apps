"""Build Anthropic tool-use schemas from the routine registry.

Each registered routine becomes one tool whose input schema is its `RoutineSpec.params`, so
the model's action space is exactly the closed set of routines (the core safety property).
Plus read-only meta-tools and — for the T2 planner — a `propose_new_routine` tool whose output
is never executed automatically.
"""
from __future__ import annotations

from typing import Any

from app_apps.routines.linear.registry import RoutineSpec

# Meta-tool names (read-only / non-actuating).
LIST_ROUTINES = "list_routines"
GET_STATUS = "get_status"
PROPOSE_NEW_ROUTINE = "propose_new_routine"

META_TOOL_NAMES = frozenset({LIST_ROUTINES, GET_STATUS, PROPOSE_NEW_ROUTINE})


def _json_type(annotation: str) -> str:
    """Map a Python annotation string (from RoutineSpec) to a JSON-schema type."""
    a = annotation.strip()
    if a == "float":
        return "number"
    if a == "int":
        return "integer"
    if a == "bool":
        return "boolean"
    if a == "str":
        return "string"
    if a and ("Sequence" in a or "List" in a or a.startswith(("list", "tuple"))):
        return "array"
    return "string"  # unknown / Optional / other — default to string


def routine_tool_schema(spec: RoutineSpec) -> dict[str, Any]:
    """An Anthropic tool dict for one routine: name, description, input_schema from params."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    bounds = spec.bounds or {}

    for p in spec.params:
        prop: dict[str, Any] = {"type": _json_type(p.annotation)}
        desc_bits = []
        if not p.required:
            desc_bits.append(f"default {p.default!r}")
        if p.name in bounds:
            lo, hi = bounds[p.name]
            prop["minimum"] = lo
            prop["maximum"] = hi
            desc_bits.append(f"range [{lo}, {hi}]")
        if desc_bits:
            prop["description"] = "; ".join(desc_bits)
        properties[p.name] = prop
        if p.required:
            required.append(p.name)

    description = spec.summary or f"Run the {spec.name} routine."
    if not spec.safe:
        description += " (moves hardware — requires confirmation)"

    return {
        "name": spec.name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def _no_input_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "required": []}


def meta_tools(*, include_planner: bool) -> list[dict[str, Any]]:
    """The read-only meta-tools, plus the planner tool when enabled."""
    tools: list[dict[str, Any]] = [
        {
            "name": LIST_ROUTINES,
            "description": "List the available routines and their parameters.",
            "input_schema": _no_input_schema(),
        },
        {
            "name": GET_STATUS,
            "description": "Report whether a routine is currently running, and which.",
            "input_schema": _no_input_schema(),
        },
    ]
    if include_planner:
        tools.append(
            {
                "name": PROPOSE_NEW_ROUTINE,
                "description": (
                    "Propose a NEW routine when no existing one fits. Returns candidate "
                    "Python source for human review; it is never run automatically."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "goal": {"type": "string", "description": "what the routine should do"},
                        "code": {
                            "type": "string",
                            "description": "a complete @routine-decorated function using lab.* verbs",
                        },
                    },
                    "required": ["name", "goal", "code"],
                },
            }
        )
    return tools


def build_tools(
    routines: dict[str, RoutineSpec], *, include_planner: bool = True
) -> list[dict[str, Any]]:
    """All tools the model may call: one per routine + meta-tools (+ planner)."""
    tools = [routine_tool_schema(spec) for spec in routines.values()]
    tools.extend(meta_tools(include_planner=include_planner))
    return tools
