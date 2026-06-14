"""Control-loop routines — feedback `@routine`s that drive a measured quantity to a target.

These are ordinary routines: they loop *measure -> PID correct -> settle* via `run_pid_lock`.
Gains/signs are parameters (empirical, tuned on hardware); the structure + convergence are
verified in software against a simulated plant. `output_limits` caps the per-step move
(slew/anti-spasm safety). Unsafe by default (they move hardware) -> the assistant must confirm.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app_apps.control.lock import LockResult, run_pid_lock
from app_apps.control.pid import PIDController
from app_apps.routines.linear.registry import routine

if TYPE_CHECKING:
    from app_apps.routines.linear.lab import Lab


@routine("lock_central_frequency")
def lock_central_frequency(
    lab: "Lab",
    target_thz: float,
    kp: float = 0.1,
    ki: float = 0.0,
    kd: float = 0.0,
    tolerance_thz: float = 0.05,
    max_iterations: int = 60,
    dt_s: float = 0.2,
    max_step_mm: float = 0.5,
) -> LockResult:
    """Feedback-lock the central frequency ν₀ by nudging the delay stage (D13/D19).

    Measures ν₀ from the SPM-002 spectrum each step and moves the delay stage until ν₀ is
    within `tolerance_thz` of `target_thz`. `max_step_mm` caps the per-step move.
    """
    pid = PIDController(
        kp=kp, ki=ki, kd=kd, setpoint=target_thz,
        output_limits=(-max_step_mm, max_step_mm),
    )

    def measure() -> float:
        return lab.fit_spectrum(lab.spectrometer.read()).nu0_thz

    def actuate(step_mm: float) -> None:
        lab.delay.move_by(step_mm)

    result = run_pid_lock(
        pid=pid,
        measure=measure,
        actuate=actuate,
        tolerance=tolerance_thz,
        max_iterations=max_iterations,
        dt=dt_s,
        sleep=lab.sleep,
    )
    lab.log(
        f"central-freq lock: converged={result.converged} "
        f"ν0={result.final_value:.4f} THz in {result.iterations} steps"
    )
    lab.record(
        target_thz=target_thz,
        final_thz=result.final_value,
        converged=result.converged,
        iterations=result.iterations,
    )
    return result
