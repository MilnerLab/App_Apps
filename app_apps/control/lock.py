"""A reusable PID feedback-lock loop — the engine behind every control-loop routine.

`run_pid_lock` is pure (no `lab`, no devices): it takes a `measure` callable (read the
controlled quantity), an `actuate` callable (apply a correction), and a `PIDController`, and
iterates measure -> correct -> settle until the measurement is within `tolerance` of the PID
setpoint or `max_iterations` is hit. Being pure makes it trivially testable against a
simulated plant; a control-loop routine just wires `measure`/`actuate` to `lab` verbs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app_apps.control.pid import PIDController


@dataclass(frozen=True)
class LockResult:
    converged: bool
    iterations: int
    final_value: float
    final_error: float


def run_pid_lock(
    *,
    pid: PIDController,
    measure: Callable[[], float],
    actuate: Callable[[float], None],
    tolerance: float,
    max_iterations: int,
    dt: float = 1.0,
    sleep: Optional[Callable[[float], None]] = None,
) -> LockResult:
    """Drive `measure()` to `pid.setpoint` via `actuate()`. Returns how it ended.

    Convergence is checked before each correction (so an already-on-target system does nothing)
    and once more after the loop. `sleep` (if given, e.g. `lab.sleep`) waits `dt` between
    corrections and is the cancellation point.
    """
    value = measure()
    for i in range(max_iterations):
        error = pid.setpoint - value
        if abs(error) <= tolerance:
            return LockResult(True, i, value, error)
        actuate(pid.update(value, dt))
        if sleep is not None:
            sleep(dt)
        value = measure()

    error = pid.setpoint - value
    return LockResult(abs(error) <= tolerance, max_iterations, value, error)
