"""Tunable defaults for the linear routine layer (timeouts, settle, XCORR reduction)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LabConfig:
    """Per-run timing/limits for the `lab` facade. All times in seconds."""

    # How long a blocking verb waits for its completion signal before RoutineTimeout.
    move_timeout_s: float = 60.0  # ESP301 stage move
    rotate_timeout_s: float = 30.0  # HWP rotate
    step_timeout_s: float = 30.0  # picomotor step
    shutter_timeout_s: float = 10.0  # servo block/unblock
    capture_timeout_s: float = 10.0  # next scope trace
    spectrum_timeout_s: float = 10.0  # next spectrometer frame

    # Bridge wake interval (cancellation/timeout latency).
    poll_s: float = 0.05

    # Optional extra dwell after a command's completion signal (0 = none).
    settle_s: float = 0.0

    # XCORR scalar reduction: mean of the N highest samples of a scope trace.
    xcorr_top_n: int = 20
