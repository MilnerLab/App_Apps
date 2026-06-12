"""
Generic PID controller for the usCFG control loops.

App-level (per D3 — not Base_Core) and dependency-free so it is trivially unit-
testable. Each control-loop Routine owns one of these (D17). Anti-spasm lives here,
not in the analysis layer (D14): deadband, output slew-rate limiting, integral
anti-windup, and output clamping.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PIDController:
    """A positional-form PID controller with practical safety features.

    Parameters
    ----------
    kp, ki, kd:
        Proportional / integral / derivative gains.
    setpoint:
        Target value of the measured variable.
    output_limits:
        ``(low, high)`` clamp on the output. ``None`` on a side disables that bound.
    deadband:
        Errors with ``abs(error) <= deadband`` produce no integral accumulation and
        hold the last output — stops the actuator hunting within measurement noise.
    slew_limit:
        Maximum change in output per second. ``None`` disables. Limits how fast the
        actuator command can move (anti-spasm).
    integral_limit:
        Optional symmetric clamp on the integral term (anti-windup). If ``None`` and
        output limits are set, the integral is back-clamped to the output range / ki.
    """

    kp: float
    ki: float = 0.0
    kd: float = 0.0
    setpoint: float = 0.0
    output_limits: tuple[float | None, float | None] = (None, None)
    deadband: float = 0.0
    slew_limit: float | None = None
    integral_limit: float | None = None

    def __post_init__(self) -> None:
        self._integral = 0.0
        self._prev_error: float | None = None
        self._last_output = 0.0
        self._has_output = False

    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear integral/derivative history. Call when (re)engaging the loop."""
        self._integral = 0.0
        self._prev_error = None
        self._last_output = 0.0
        self._has_output = False

    @property
    def last_output(self) -> float:
        return self._last_output

    def update(self, measurement: float, dt: float) -> float:
        """Advance one step and return the (clamped, slew-limited) control output.

        ``dt`` is the elapsed time in seconds since the previous call.
        """
        if dt <= 0.0:
            return self._last_output

        error = self.setpoint - measurement

        # Deadband: inside the band, freeze the actuator (hold last output).
        if abs(error) <= self.deadband:
            self._prev_error = error
            return self._last_output

        # Integral with anti-windup clamp.
        self._integral += error * dt
        self._clamp_integral()

        # Derivative on error.
        if self._prev_error is None:
            derivative = 0.0
        else:
            derivative = (error - self._prev_error) / dt
        self._prev_error = error

        raw = self.kp * error + self.ki * self._integral + self.kd * derivative
        output = self._apply_slew(self._clamp_output(raw), dt)

        self._last_output = output
        self._has_output = True
        return output

    # ------------------------------------------------------------------

    def _clamp_integral(self) -> None:
        limit = self.integral_limit
        if limit is None and self.ki != 0.0:
            lo, hi = self.output_limits
            if lo is not None and hi is not None:
                limit = max(abs(lo), abs(hi)) / abs(self.ki)
        if limit is not None:
            self._integral = _clamp(self._integral, -limit, limit)

    def _clamp_output(self, value: float) -> float:
        lo, hi = self.output_limits
        return _clamp(value, lo, hi)

    def _apply_slew(self, target: float, dt: float) -> float:
        if self.slew_limit is None or not self._has_output:
            return target
        max_step = self.slew_limit * dt
        delta = _clamp(target - self._last_output, -max_step, max_step)
        return self._last_output + delta


def _clamp(value: float, lo: float | None, hi: float | None) -> float:
    if lo is not None and value < lo:
        return lo
    if hi is not None and value > hi:
        return hi
    return value
