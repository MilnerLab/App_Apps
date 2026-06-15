"""Control-loop routines — feedback `@routine`s that drive a measured quantity to a target.

These are ordinary routines: they loop *measure -> PID correct -> settle* via `run_pid_lock`.
Gains/signs are parameters (empirical, tuned on hardware); the structure + convergence are
verified in software against a simulated plant. `output_limits` caps the per-step move
(slew/anti-spasm safety). Unsafe by default (they move hardware) -> the assistant must confirm.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from base_core.math.enums import AngleUnit
from base_core.math.models import Angle

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


@routine("lock_terminal_frequency")
def lock_terminal_frequency(
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
    """Feedback-lock the terminal frequency ν_end by nudging the truncation stage (D19/Q13).

    Measures ν_end (the red-edge half-max frequency) from the SPM-002 spectrum each step and
    moves the truncation stage until ν_end is within `tolerance_thz` of `target_thz`.
    `max_step_mm` caps the per-step move. Same shape as `lock_central_frequency` (delay→ν₀).
    """
    pid = PIDController(
        kp=kp, ki=ki, kd=kd, setpoint=target_thz,
        output_limits=(-max_step_mm, max_step_mm),
    )

    def measure() -> float:
        return lab.fit_spectrum(lab.spectrometer.read()).nu_end_thz

    def actuate(step_mm: float) -> None:
        lab.truncation.move_by(step_mm)

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
        f"terminal-freq lock: converged={result.converged} "
        f"ν_end={result.final_value:.4f} THz in {result.iterations} steps"
    )
    lab.record(
        target_thz=target_thz,
        final_thz=result.final_value,
        converged=result.converged,
        iterations=result.iterations,
    )
    return result


@routine("lock_phase")
def lock_phase(
    lab: "Lab",
    target_rad: float,
    start_angle_rad: float = 0.0,
    kp: float = 0.1,
    ki: float = 0.0,
    kd: float = 0.0,
    tolerance_rad: float = 0.02,
    max_iterations: int = 60,
    dt_s: float = 0.2,
    max_step_rad: float = 0.1,
) -> LockResult:
    """Feedback-hold the initial fringe phase φ₀ by rotating the HWP (RGV100BL, D19/Q13).

    Measures `phase0` from the SPM-002 spectrum each step and rotates the HWP to drive φ₀ toward
    `target_rad`. The HWP exposes only absolute `rotate_to` with no read-back, so we track the
    commanded angle here, starting at `start_angle_rad`. `max_step_rad` caps the per-step
    rotation. The angle↔phase gain/sign is empirical (`kp`), tuned on hardware.
    """
    pid = PIDController(
        kp=kp, ki=ki, kd=kd, setpoint=target_rad,
        output_limits=(-max_step_rad, max_step_rad),
    )
    commanded = [start_angle_rad]

    def measure() -> float:
        return lab.fit_spectrum(lab.spectrometer.read()).phase0

    def actuate(step_rad: float) -> None:
        commanded[0] += step_rad
        lab.hwp.rotate_to(Angle(commanded[0], AngleUnit.RAD))

    result = run_pid_lock(
        pid=pid,
        measure=measure,
        actuate=actuate,
        tolerance=tolerance_rad,
        max_iterations=max_iterations,
        dt=dt_s,
        sleep=lab.sleep,
    )
    lab.log(
        f"phase lock: converged={result.converged} "
        f"φ0={result.final_value:.4f} rad in {result.iterations} steps"
    )
    lab.record(
        target_rad=target_rad,
        final_rad=result.final_value,
        final_angle_rad=commanded[0],
        converged=result.converged,
        iterations=result.iterations,
    )
    return result
