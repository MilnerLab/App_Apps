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
    #: Added to every probe *base* position at this setpoint to get the commanded
    #: probe position: ``grating_mm + probe_intercept_mm``. The probe overlap tracks
    #: the grating one-to-one, so this shifts the whole base sweep per grating step.
    probe_offset_mm: float
    #: This setpoint's probe *base* sweep — the grating-independent delay axis. Its
    #: step is Nyquist-matched to this setpoint's own top frequency when adaptive
    #: stepping is on, so different setpoints can have different densities (and point
    #: counts). Commanded position per point is ``base + probe_offset_mm``.
    probe_base_mm: tuple[float, ...]
    #: The step used to build ``probe_base_mm`` — recorded for provenance.
    probe_step_mm: float
    #: The modelled highest instantaneous frequency at this setpoint, GHz — what the
    #: step was matched to. Zero at zero separation and zero delay offset.
    max_freq_ghz: float

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

    #: Flattened outer×inner grid, in execution order. Each setpoint carries its own
    #: probe sweep (``Setpoint.probe_base_mm``) — sweeps are no longer shared, because
    #: adaptive stepping can give each setpoint a different density.
    setpoints: tuple[Setpoint, ...]
    #: ``"grating"`` or ``"delay"`` — which axis got the outer loop, and why.
    outer_axis: str
    outer_reason: str
    #: Non-fatal plan-time concerns, surfaced to the operator and stored as provenance.
    warnings: tuple[str, ...] = ()

    @property
    def n_points(self) -> int:
        """Total probe points across the whole run — the basis for any ETA.

        Summed per setpoint, since adaptive stepping means setpoints can differ in
        how many probe points they carry.
        """
        return sum(len(sp.probe_base_mm) for sp in self.setpoints)

    @property
    def probe_step_range_mm(self) -> tuple[float, float]:
        """(finest, coarsest) probe step actually used across the run."""
        steps = [sp.probe_step_mm for sp in self.setpoints]
        return (min(steps), max(steps))


def expand_range(
    start: float, stop: float, step: float, *, name: str, include_endpoint: bool = False
) -> tuple[float, ...]:
    """Inclusive range from ``start`` to ``stop`` in increments of ``step``.

    ``step`` is unsigned; direction comes from ``stop - start``. ``stop`` is
    included when the step divides the interval to within a relative tolerance —
    without that, ``0..10`` by ``0.1`` would silently drop its endpoint to float
    error. Positions are computed as ``start + i*step`` rather than accumulated,
    so error does not grow along the scan.

    With ``include_endpoint`` the true ``stop`` is *always* the last position, even
    when the step does not divide the interval — it is appended as a final, shorter
    step. Used for the probe sweep so every setpoint ends exactly on ``probe_stop_mm``
    regardless of its adaptive step, which lets analysis interpolate onto and truncate
    to one common right edge. Left off for grating/delay, where a stray short final
    step on a physical outer axis is undesirable.
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
    positions = [start + direction * step * i for i in range(count + 1)]
    # Append the exact endpoint when the truncating step fell short of it (a genuine
    # miss, not float noise the snap above already absorbed).
    if include_endpoint and not math.isclose(positions[-1], stop, rel_tol=1e-9, abs_tol=1e-9):
        positions.append(stop)
    return tuple(positions)


#: Speed of light in mm/s — for the double-pass Nyquist step.
_C_MM_PER_S = 299_792_458_000.0


def max_frequency_hz(cfg: XcorrConfig, grating_mm: float, delay_base_mm: float) -> float:
    """Modelled highest instantaneous frequency at a setpoint, in Hz.

    ``central + bandwidth/2`` from the rough calibration in :class:`XcorrConfig`.
    Central frequency is set by the delay offset (zero at ``delay_base = 0``);
    bandwidth by the acceleration, i.e. how far the grating is from zero separation.
    """
    central_ghz = cfg.freq_per_delay_ghz_per_mm * abs(delay_base_mm)
    bandwidth_ghz = cfg.freq_bw_ghz_per_grating_mm * abs(grating_mm - cfg.grating_zero_mm)
    return (central_ghz + bandwidth_ghz / 2.0) * 1e9


def probe_step_for(cfg: XcorrConfig, grating_mm: float, delay_base_mm: float) -> float:
    """The probe step, mm, for one setpoint.

    Fixed at ``probe_step_mm`` unless adaptive stepping is on, in which case it is the
    double-pass Nyquist step ``c/(4 f)`` oversampled by ``probe_oversample`` and
    clamped to ``[probe_step_mm, probe_step_max_mm]``. At zero frequency the carrier
    vanishes and the step is capped at ``probe_step_max_mm``.
    """
    if not cfg.adaptive_probe_step:
        return cfg.probe_step_mm
    f = max_frequency_hz(cfg, grating_mm, delay_base_mm)
    if f <= 0.0:
        return cfg.probe_step_max_mm
    step = _C_MM_PER_S / (4.0 * f) / cfg.probe_oversample
    return min(max(step, cfg.probe_step_mm), cfg.probe_step_max_mm)


def _build_setpoint(
    cfg: XcorrConfig, grating_mm: float, delay_base_mm: float, gi: int, di: int
) -> "Setpoint":
    """Assemble one setpoint, including its Nyquist-matched probe sweep."""
    step = probe_step_for(cfg, grating_mm, delay_base_mm)
    # include_endpoint: every setpoint's probe sweep ends exactly on probe_stop_mm even
    # under adaptive stepping, so all setpoints share one right edge for analysis.
    probe_base = expand_range(
        cfg.probe_start_mm, cfg.probe_stop_mm, step, name="probe", include_endpoint=True
    )
    f_ghz = max_frequency_hz(cfg, grating_mm, delay_base_mm) / 1e9
    correction = cfg.delay_slope * grating_mm + cfg.delay_intercept_mm
    return Setpoint(
        grating_index=gi,
        delay_index=di,
        grating_mm=grating_mm,
        delay_mm=delay_base_mm + correction,
        delay_base_mm=delay_base_mm,
        delay_correction_mm=correction,
        probe_offset_mm=grating_mm + cfg.probe_intercept_mm,
        probe_base_mm=probe_base,
        probe_step_mm=step,
        max_freq_ghz=f_ghz,
    )


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
    grating = expand_range(
        cfg.grating_start_mm, cfg.grating_stop_mm, cfg.grating_step_mm, name="grating"
    )
    delay_base = expand_range(
        cfg.delay_base_start_mm, cfg.delay_base_stop_mm, cfg.delay_base_step_mm, name="delay"
    )

    if cfg.n_traces < 1:
        raise PlanError(f"n_traces must be >= 1, got {cfg.n_traces}")
    if cfg.adaptive_probe_step and cfg.probe_step_max_mm < cfg.probe_step_mm:
        raise PlanError(
            f"probe_step_max_mm ({cfg.probe_step_max_mm}) is finer than the floor "
            f"probe_step_mm ({cfg.probe_step_mm}); the clamp is inverted."
        )
    if cfg.adaptive_probe_step and cfg.probe_oversample <= 0.0:
        # probe_step_for divides by this; <= 0 would be a ZeroDivisionError (or a
        # negative step) deep in setpoint expansion. Refuse it here as a plan error
        # rather than let it crash mid-plan (defect G25).
        raise PlanError(
            f"probe_oversample must be > 0, got {cfg.probe_oversample}"
        )

    _check_limits(grating, "grating", "grating")

    # Only the *corrected* positions are ever commanded, so only they are worth
    # validating. The probe step affects density, not the endpoints, so validating
    # the [start, stop] extremes against every grating covers every commanded probe
    # position regardless of adaptive stepping. A base range that is legal on its own
    # but illegal once the grating tracking is applied is exactly what this catches.
    probe_extremes = (cfg.probe_start_mm, cfg.probe_stop_mm)
    corrected_probe = tuple(
        e + g + cfg.probe_intercept_mm
        for g in grating
        for e in probe_extremes
    )
    _check_limits(corrected_probe, "probe", "grating-tracked probe")

    corrected_delay = tuple(
        base + cfg.delay_slope * g + cfg.delay_intercept_mm
        for g in grating
        for base in delay_base
    )
    _check_limits(corrected_delay, "delay", "grating-corrected delay")

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
        _build_setpoint(cfg, grating[gi], delay_base[di], gi, di)
        for gi, di in pairs
    )

    return ScanPlan(
        setpoints=setpoints,
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
