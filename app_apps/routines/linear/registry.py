"""The `@routine` decorator and the routine registry.

A routine author writes a plain function whose first parameter is the injected `lab` facade::

    @routine("delay_freq_sweep")
    def delay_freq_sweep(lab, start_mm: float, stop_mm: float, step_mm: float = 0.1):
        ...

Decorating registers a `RoutineSpec` (the function plus introspected metadata) under a name.
The runner (R.4) looks routines up by name, injects `lab` + the remaining parameters, and
runs the function on a background thread. Authors never write a `BaseModule` or touch DI.

The captured parameter metadata (name / type / default / required) is what a UI form or an
LLM uses to know what arguments a routine takes.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class RoutineRegistrationError(Exception):
    """Raised when a routine cannot be registered (bad signature, name collision)."""


class RoutineNotFound(KeyError):
    """Raised when no routine is registered under a requested name."""


@dataclass(frozen=True)
class RoutineParam:
    """One author-supplied parameter of a routine (everything after `lab`)."""

    name: str
    annotation: str  # e.g. "float", "int"; "" if unannotated
    required: bool  # True if it has no default
    default: Any = None  # meaningful only when required is False


@dataclass(frozen=True)
class RoutineSpec:
    """A registered routine: its callable plus introspected metadata."""

    name: str
    func: Callable[..., None]
    summary: str  # first line of the docstring ("" if none)
    doc: str  # full docstring ("" if none)
    params: tuple[RoutineParam, ...] = field(default_factory=tuple)

    def __call__(self, lab: Any, *args: Any, **kwargs: Any) -> None:
        """Convenience: invoke the underlying function with the injected facade."""
        return self.func(lab, *args, **kwargs)


_REGISTRY: dict[str, RoutineSpec] = {}


def _annotation_str(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return ""
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def _build_spec(name: str, func: Callable[..., None]) -> RoutineSpec:
    sig = inspect.signature(func)
    parameters = list(sig.parameters.values())
    if not parameters:
        raise RoutineRegistrationError(
            f"routine {name!r} must take the injected `lab` facade as its first parameter"
        )

    params: list[RoutineParam] = []
    for p in parameters[1:]:  # skip the first (the injected `lab`)
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            # *args / **kwargs don't map to a form field; ignore for metadata.
            continue
        required = p.default is inspect.Parameter.empty
        params.append(
            RoutineParam(
                name=p.name,
                annotation=_annotation_str(p.annotation),
                required=required,
                default=None if required else p.default,
            )
        )

    doc = inspect.getdoc(func) or ""
    summary = doc.splitlines()[0] if doc else ""
    return RoutineSpec(
        name=name, func=func, summary=summary, doc=doc, params=tuple(params)
    )


def _same_function(a: Callable[..., Any], b: Callable[..., Any]) -> bool:
    # Treat a re-import of the same source function as the same routine, so module
    # reloads don't raise spurious name-collision errors.
    return (
        getattr(a, "__qualname__", None) == getattr(b, "__qualname__", None)
        and getattr(a, "__module__", None) == getattr(b, "__module__", None)
    )


def routine(
    name: Optional[str | Callable[..., None]] = None,
) -> Callable[..., None]:
    """Register a function as a named routine.

    Usable as `@routine` (name defaults to the function name) or `@routine("name")`.
    Returns the original function unchanged, so it stays directly callable/testable.
    """

    def decorator(func: Callable[..., None]) -> Callable[..., None]:
        routine_name = func.__name__ if name is None or callable(name) else name
        spec = _build_spec(routine_name, func)
        existing = _REGISTRY.get(routine_name)
        if existing is not None and not _same_function(existing.func, func):
            raise RoutineRegistrationError(
                f"a different routine is already registered under {routine_name!r}"
            )
        _REGISTRY[routine_name] = spec
        return func

    # Bare @routine usage: `name` is actually the decorated function.
    if callable(name):
        return decorator(name)
    return decorator


def all_routines() -> dict[str, RoutineSpec]:
    """Return a copy of the registry (mutating it does not affect registration)."""
    return dict(_REGISTRY)


def routine_names() -> list[str]:
    return sorted(_REGISTRY)


def get_routine(name: str) -> RoutineSpec:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise RoutineNotFound(name) from None


def clear_registry() -> None:
    """Remove all registrations (intended for tests)."""
    _REGISTRY.clear()
