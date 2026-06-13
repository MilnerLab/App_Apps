"""Linear (physicist/LLM-friendly) routine-authoring layer.

A routine is written as a plain top-to-bottom function that *blocks* on each device
call, instead of the async/callback/state-machine pattern of the base framework. This
package provides the async->sync bridge that makes that safe and the registry/runner that
adapt such functions to the app lifecycle.

See `docs/routine_authoring_plan.md` for the full design. This module (R.1) contains only
the cancellation primitives and the blocking bridge — no device wiring yet.
"""

from app_apps.routines.linear.cancel import (
    CancelToken,
    RoutineCancelled,
    RoutineError,
    RoutineTimeout,
)

__all__ = [
    "CancelToken",
    "RoutineCancelled",
    "RoutineError",
    "RoutineTimeout",
]
