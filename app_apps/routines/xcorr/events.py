"""Domain events published by :class:`XcorrRoutine`.

These are the routine's only outward channel. The headless runner exits on
``XcorrFinished`` / ``XcorrFailed``; the ViewModel will later subscribe to the same
three events for progress and completion. Nothing that observes a run may require
Qt — a routine must be fully driveable from the container and the bus (AGENTS.md §6.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class XcorrScanStarted:
    """Published once, right after the plan is built and before anything moves.

    Carries the axis ranges and totals a live display needs to show
    position-within-range and overall progress, so the panel never has to read the
    config or wait to infer the ranges from streamed points. Ranges are (min, max),
    grating/delay in stage mm and probe in *base* mm (0..probe_stop)."""

    grating_range_mm: tuple[float, float]
    delay_base_range_mm: tuple[float, float]
    probe_base_range_mm: tuple[float, float]
    n_points: int
    n_setpoints: int


@dataclass(frozen=True)
class XcorrProgress:
    """Published once per completed probe point.

    ``points_done`` / ``n_points`` are carried explicitly rather than derived: with
    adaptive stepping each setpoint can have a different number of probe points, so
    ``setpoint_index * n_probe`` no longer gives a run-wide count. ``n_probe`` is the
    count for *this* setpoint.
    """

    setpoint_index: int
    n_setpoints: int
    probe_index: int
    n_probe: int
    points_done: int
    n_points: int
    grating_mm: float
    delay_mm: float
    probe_mm: float
    v_mean_pos: float
    #: The setpoint's BASE delay (before the grating-tracking correction). The display
    #: panel's grid-summary plots key on this (R14: Δt = 2·delay_base/c), and it cannot
    #: be recovered from ``delay_mm`` without the run's slope/intercept — so it is carried
    #: on the event rather than re-derived. Constant across a setpoint's probe sweep.
    delay_base_mm: float = 0.0
    #: The probe BASE position for this point (``probe_mm`` is the commanded value
    #: ``base + grating + intercept``). Carried so the position header can show the probe
    #: within its base range without knowing the run's intercept.
    probe_base_mm: float = 0.0


@dataclass(frozen=True)
class XcorrGroupWritten:
    """One ``(grating, delay)`` combination is complete and flushed to disk (R4)."""

    group_name: str
    setpoint_index: int
    n_setpoints: int
    n_rows: int


@dataclass(frozen=True)
class XcorrFinished:
    """The run ended without an unhandled error. ``aborted`` distinguishes the cases."""

    path: str
    aborted: bool
    n_groups_written: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class XcorrFailed:
    """The run stopped on an error. Partial data up to the last flush is on disk."""

    error: str
    path: str = ""
    n_groups_written: int = 0
