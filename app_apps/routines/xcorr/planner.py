"""Scan planning for XCORR — a pure function over :class:`XcorrConfig`.

No hardware, no IPC, no bus. That is deliberate: every setpoint the run will ever
command is computed and validated *here*, before anything moves (R2/S1), and this
is the one component in the routine cheap enough to unit-test decisively.

Two structural decisions live in this module:

* **The grid is flattened at plan time.** ``BaseRoutine``'s step list is linear and
  a nested loop does not map onto it (gap G-C). ``ScanPlan.setpoints`` is the
  fully-expanded outer×inner product in execution order; the routine iterates it.
* **Loop order is decided here, not at runtime**, and recorded in the plan so it
  reaches the run's provenance (D5).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app_apps.routines.xcorr.config import (
    AXIS_LIMITS,
    LIMIT_TOLERANCE_MM,
    SLOPE_DRAG_WARN_FRACTION,
    XcorrConfig,
)


class PlanError(ValueError):
    """The configuration cannot produce a valid scan. Raised before any motion."""


@dataclass(frozen=True)
class Setpoint:
    """One ``(grating, delay)`` combination — one HDF5 group, one probe sweep."""

    grating_index: int
    delay_index: int
    grating_mm: float
    #: The commanded delay position: ``delay_base_mm + delay_correction_mm``.
    delay_mm: float
    delay_base_mm: float
    delay_correction_mm: float

    @property
    def group_name(self) -> str:
        """Zero-padded and index-first, so ``sorted(f["/scans"])`` yields **grid**
        order — grating-major, then delay.

        Note this is *not* necessarily execution order. XCORR_SPEC.md §6.1 claims it
        is; that holds only when the grating took the outer loop. Under delay-outer
        (D5 picks whichever axis has fewer steps) the file is written in a different
        sequence than it sorts. Grid order is the more useful of the two for
        analysis, and every group carries its own coordinates as attributes, so
        nothing depends on the distinction — but do not read sort order as a record
        of what ran when. ``ScanPlan.outer_axis`` is stored in the run's provenance
        for exactly that purpose.
        """
        return f"g{self.grating_index:04d}_d{self.delay_index:04d}"


@dataclass(frozen=True)
class ScanPlan:
    """Every position the run will command, in the order it will command them."""

    #: Flattened outer×inner grid, in execution order.
    setpoints: tuple[Setpoint, ...]
    #: The probe sweep, identical at every setpoint.
    probe_mm: tuple[float, ...]
    #: ``"grating"`` or ``"delay"`` — which axis got the outer loop, and why.
    outer_axis: str
    outer_reason: str
    #: Non-fatal plan-time concerns, surfaced to the operator and stored as provenance.
    warnings: tuple[str, ...] = ()

    @property
    def n_points(self) -> int:
        """Total probe points in the run — the basis for any ETA."""
        return len(self.setpoints) * len(self.probe_mm)


def expand_range(start: float, stop: float, step: float, *, name: str) -> tuple[float, ...]:
    """Inclusive range from ``start`` to ``stop`` in increments of ``step``.

    ``step`` is unsigned; direction comes from ``stop - start``. ``stop`` is
    included when the step divides the interval to within a relative tolerance —
    without that, ``0..10`` by ``0.1`` would silently drop its endpoint to float
    error. Positions are computed as ``start + i*step`` rather than accumulated,
    so error does not grow along the scan.
    """
    if step <= 0:
        raise PlanError(f"{name}: step must be > 0, got {step}")

    span = stop - start
    if span == 0.0:
        return (start,)

    n_steps = abs(span) / step
    # Snap to an integer count when we are within a hair of one, so the endpoint
    # survives; otherwise truncate, leaving the last point short of `stop`.
    n_int = round(n_steps)
    count = n_int if math.isclose(n_steps, n_int, rel_tol=1e-9, abs_tol=1e-9) else int(n_steps)

    direction = math.copysign(1.0, span)
    return tuple(start + direction * step * i for i in range(count + 1))


def _check_limits(positions: tuple[float, ...], role: str, label: str) -> None:
    lo, hi = AXIS_LIMITS[role]
    for i, p in enumerate(positions):
        if p < lo - LIMIT_TOLERANCE_MM or p > hi + LIMIT_TOLERANCE_MM:
            raise PlanError(
                f"{label} setpoint #{i} = {p:.4f} mm is outside the {role} stage's "
                f"soft limits [{lo}, {hi}] mm. Refusing to start."
            )


def plan_scan(cfg: XcorrConfig) -> ScanPlan:
    """Expand, correct, order, flatten and validate. Raises :class:`PlanError`.

    Every setpoint in the returned plan is known to be inside its stage's soft
    limits, so the routine never has to check again mid-run.
    """
    probe = expand_range(cfg.probe_start_mm, cfg.probe_stop_mm, cfg.probe_step_mm, name="probe")
    grating = expand_range(
        cfg.grating_start_mm, cfg.grating_stop_mm, cfg.grating_step_mm, name="grating"
    )
    delay_base = expand_range(
        cfg.delay_base_start_mm, cfg.delay_base_stop_mm, cfg.delay_base_step_mm, name="delay"
    )

    if cfg.n_traces < 1:
        raise PlanError(f"n_traces must be >= 1, got {cfg.n_traces}")

    _check_limits(probe, "probe", "probe")
    _check_limits(grating, "grating", "grating")

    # Only the *corrected* delay is ever commanded, so only it is worth validating.
    # A base range that is legal on its own but illegal once corrected is exactly
    # the mistake this catches.
    corrected = tuple(
        base + cfg.delay_slope * g + cfg.delay_intercept_mm
        for g in grating
        for base in delay_base
    )
    _check_limits(corrected, "delay", "grating-corrected delay")

    warnings = _plan_warnings(cfg, grating, delay_base)

    # D5: the axis with fewer steps takes the outer loop, because the outer loop is
    # commanded fewest times. On a tie prefer grating-outer — it lets the delay
    # stage sweep its base range monotonically instead of jumping back once per
    # outer iteration, which is the backlash-friendlier order (§4.3).
    grating_outer = len(grating) <= len(delay_base)
    if grating_outer:
        outer_axis = "grating"
        reason = (
            f"grating has {len(grating)} step(s) vs delay's {len(delay_base)}"
            if len(grating) < len(delay_base)
            else f"tie at {len(grating)} step(s); grating-outer sweeps delay monotonically"
        )
    else:
        outer_axis = "delay"
        reason = f"delay has {len(delay_base)} step(s) vs grating's {len(grating)}"

    pairs = (
        [(gi, di) for gi in range(len(grating)) for di in range(len(delay_base))]
        if grating_outer
        else [(gi, di) for di in range(len(delay_base)) for gi in range(len(grating))]
    )

    setpoints = tuple(
        Setpoint(
            grating_index=gi,
            delay_index=di,
            grating_mm=grating[gi],
            delay_mm=delay_base[di] + cfg.delay_slope * grating[gi] + cfg.delay_intercept_mm,
            delay_base_mm=delay_base[di],
            delay_correction_mm=cfg.delay_slope * grating[gi] + cfg.delay_intercept_mm,
        )
        for gi, di in pairs
    )

    return ScanPlan(
        setpoints=setpoints,
        probe_mm=probe,
        outer_axis=outer_axis,
        outer_reason=reason,
        warnings=warnings,
    )


def _plan_warnings(
    cfg: XcorrConfig,
    grating: tuple[float, ...],
    delay_base: tuple[float, ...],
) -> tuple[str, ...]:
    """Non-fatal concerns worth telling the operator before a multi-hour run."""
    out: list[str] = []

    # Validate the small-slope invariant rather than assuming it (§4.3).
    if len(grating) > 1 and len(delay_base) > 1:
        drag = abs(cfg.delay_slope) * cfg.grating_step_mm
        if drag > SLOPE_DRAG_WARN_FRACTION * cfg.delay_base_step_mm:
            out.append(
                f"delay_slope={cfg.delay_slope} drags the delay stage by {drag:.4f} mm "
                f"per grating step, which is more than {SLOPE_DRAG_WARN_FRACTION:.0%} of "
                f"the delay's own step ({cfg.delay_base_step_mm} mm). The physical "
                f"correction is ~0.005 mm/mm — check the slope is not misconfigured."
            )

    return tuple(out)
