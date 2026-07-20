"""Configuration for the XCORR cross-correlation scan.

Everything here either changes what the hardware does or where the output lands.
Values with exactly one correct answer — COM port, axis numbers, VISA resource,
soft limits — are module constants below, not configuration. They were read live
from the instruments (XCORR_SPEC.md §3.1/§3.3) and have not changed since the
hardware was installed; exposing them as parameters would add a widget and a
failure mode (a wrong axis number sends the *probe* stage on a 300 mm move) for
something nobody will ever set.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Per-role soft limits in mm, read live from the ESP301 on 2026-07-19
#: (``SL?``/``SR?``). Keyed by the role names used throughout the routine, which
#: are also the ``XcorrRoutine`` constructor parameter names — the role binding
#: *is* the constructor signature, so there is no axis-role indirection layer.
#:
#:   probe   = axis 1, FMS300PP   (scanned)
#:   delay   = axis 2, MFA-CC     (central frequency)
#:   grating = axis 3, UTS150CC   (chirp difference)
AXIS_LIMITS: dict[str, tuple[float, float]] = {
    "probe": (-9.5, 290.5),
    "delay": (0.0, 25.0),
    "grating": (-75.0, 75.0),
}

#: Tektronix TDS 2012C, USBTMC. Verified 2026-07-20; note this is a TDS, not the
#: TBS2012C that ``Devices/oscilloscope/tbs_driver.py`` targets (defect G8).
SCOPE_RESOURCE = "USB0::0x0699::0x03A3::C015100::INSTR"

#: Absolute tolerance, in mm, for deciding whether a setpoint sits on a limit.
#: Range expansion accumulates float error, so an endpoint that is mathematically
#: exactly on a limit can land a few ULP outside it. Far below the stages'
#: resolution, so it cannot mask a real violation.
LIMIT_TOLERANCE_MM = 1e-9

#: Warn if the grating-tracking correction drags the delay stage by more than this
#: fraction of the delay's own step size. The physical correction is ~0.005 mm/mm
#: (0.05 mm of delay per 10 mm of grating travel) and a much larger slope means the
#: experiment does not work at all — so a large drag is the signature of a
#: misconfigured correction, and it is cheap to catch at plan time (§4.3).
SLOPE_DRAG_WARN_FRACTION = 0.5


@dataclass(frozen=True)
class XcorrConfig:
    """One XCORR run.

    Ranges are inclusive of ``stop`` where the step divides the interval evenly.
    ``step`` is always positive; the direction of travel comes from the sign of
    ``stop - start``, so a descending scan is written ``start=10, stop=0, step=1``.
    """

    # --- probe (axis 1) — the scanned axis -----------------------------------
    probe_start_mm: float
    probe_stop_mm: float
    probe_step_mm: float

    # --- grating (axis 3) — chirp difference ---------------------------------
    grating_start_mm: float
    grating_stop_mm: float
    grating_step_mm: float

    # --- delay (axis 2) — central frequency ----------------------------------
    # The *base* range. The commanded position is base + slope*grating + intercept
    # (§4.2); it is the corrected value that is validated against AXIS_LIMITS.
    delay_base_start_mm: float
    delay_base_stop_mm: float
    delay_base_step_mm: float

    #: Grating-tracking correction, mm of delay per mm of grating. See §4.3 — small
    #: slope is an invariant of the experiment, not merely a default.
    delay_slope: float = 0.0
    #: Constant offset of the correction, mm.
    delay_intercept_mm: float = 0.0

    out_dir: Path = Path(".")

    #: Traces averaged per probe point. Must be software-averaged: the TDS2012C's
    #: ``ACQuire:NUMAVg`` accepts only 4/16/64/128 (D2).
    n_traces: int = 10

    #: Dwell after each move before acquiring, seconds. ``MD?`` motion-done may or
    #: may not include settling; rather than trusting the flag, dwell explicitly.
    settle_s: float = 0.0

    #: Per-request timeout for a blocking move or acquisition, seconds.
    timeout_s: float = 130.0

    #: Scope channel to acquire. The TDS2012C has 2.
    channel: int = 1
