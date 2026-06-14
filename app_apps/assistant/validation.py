"""Validate/coerce LLM-proposed parameters against a routine's RoutineSpec.

The LLM can only call registered routines (closed verb set); this is the second gate —
it rejects unknown params, missing required params, type-incoherent values, and out-of-bounds
numbers *before* anything is launched. On failure the caller hands the error list back to the
model for one self-correction attempt (see assistant.py).
"""
from __future__ import annotations

from typing import Any

from app_apps.routines.linear.registry import RoutineParam, RoutineSpec


class ParamValidationError(Exception):
    """Raised when proposed parameters don't satisfy a routine's spec. Carries `errors`."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _coerce(param: RoutineParam, value: Any) -> Any:
    """Best-effort coercion to the param's annotated type. Raises ValueError on mismatch."""
    ann = param.annotation
    if ann == "float":
        if isinstance(value, bool):  # bool is an int subclass; reject as a number
            raise ValueError("expected a number")
        return float(value)
    if ann == "int":
        if isinstance(value, bool):
            raise ValueError("expected an integer")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("expected an integer")
        return int(value)
    if ann == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise ValueError("expected a boolean")
    if ann == "str":
        return str(value)
    if ann and ("Sequence" in ann or "List" in ann or ann.startswith(("list", "tuple"))):
        if not isinstance(value, (list, tuple)):
            raise ValueError("expected a list")
        return list(value)
    return value  # unknown/Optional/other — pass through


def validate_params(spec: RoutineSpec, args: dict[str, Any]) -> dict[str, Any]:
    """Return coerced kwargs for `spec`, or raise ParamValidationError with all problems."""
    errors: list[str] = []
    known = {p.name: p for p in spec.params}

    for key in args:
        if key not in known:
            errors.append(f"unknown parameter {key!r}")

    coerced: dict[str, Any] = {}
    for param in spec.params:
        if param.name in args:
            try:
                coerced[param.name] = _coerce(param, args[param.name])
            except (ValueError, TypeError):
                errors.append(
                    f"parameter {param.name!r} must be {param.annotation or 'a value'} "
                    f"(got {args[param.name]!r})"
                )
        elif param.required:
            errors.append(f"missing required parameter {param.name!r}")

    if spec.bounds:
        for name, (lo, hi) in spec.bounds.items():
            val = coerced.get(name)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                if not (lo <= val <= hi):
                    errors.append(f"parameter {name!r}={val} is outside bounds [{lo}, {hi}]")

    if errors:
        raise ParamValidationError(errors)
    return coerced
