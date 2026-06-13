"""Reference linear routines — the workhorse probe scans physicists/LLMs copy from.

Everything here uses only the `lab` verb grammar (docs/experiment_physics.md §2.7). Launch via
the runner, e.g.::

    runner.launch("probe_xcorr_scan", start_mm=0.0, stop_mm=5.0, step_mm=0.05)

A routine is just a function whose first argument is the injected `lab`; every device call
blocks until it completes, so the code reads top-to-bottom.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Sequence

from app_apps.routines.linear.registry import routine

if TYPE_CHECKING:
    from app_apps.routines.linear.lab import Lab


@routine("probe_xcorr_scan")
def probe_xcorr_scan(
    lab: "Lab",
    start_mm: float,
    stop_mm: float,
    step_mm: float,
    save_path: str = "probe_xcorr_scan.csv",
    plot_path: Optional[str] = None,
) -> str:
    """Scan the probe stage; record the XCORR scalar (mean of top-N CH1 samples) vs position.

    At each probe position: move (blocks until settled), capture the CH1 photodiode trace and
    reduce it to one XCORR point, and record the pair. Saves a CSV; optionally a PNG.
    """
    lab.log(f"probe XCORR scan: {start_mm}..{stop_mm} mm, step {step_mm}")
    for position_mm in lab.frange(start_mm, stop_mm, step_mm):
        lab.probe.move_to(position_mm)
        lab.record(probe_mm=position_mm, xcorr=lab.xcorr_point())
    path = lab.save(save_path)
    if plot_path is not None:
        lab.plot("probe_mm", "xcorr", save_path=plot_path)
    lab.log(f"probe XCORR scan done: {len(lab.records)} points -> {path}")
    return path


@routine("probe_scan_with_spectrum")
def probe_scan_with_spectrum(
    lab: "Lab",
    start_mm: float,
    stop_mm: float,
    step_mm: float,
    save_path: str = "probe_scan_spectrum.csv",
) -> str:
    """Like probe_xcorr_scan, but also fit the SPM-002 spectrum at each probe position.

    Records the XCORR scalar plus the fitted central frequency and span per point.
    """
    for position_mm in lab.frange(start_mm, stop_mm, step_mm):
        lab.probe.move_to(position_mm)
        xcorr = lab.xcorr_point()
        info = lab.fit_spectrum(lab.spectrometer.read())
        lab.record(
            probe_mm=position_mm,
            xcorr=xcorr,
            nu0_thz=info.nu0_thz,
            span_thz=info.nu_start_thz - info.nu_end_thz,
        )
    return lab.save(save_path)


@routine("overnight_central_freq_series")
def overnight_central_freq_series(
    lab: "Lab",
    delay_setpoints_mm: Sequence[float],
    start_mm: float,
    stop_mm: float,
    step_mm: float,
    min_xcorr: float = 0.0,
    save_path: str = "overnight_series.csv",
) -> str:
    """Overnight: for each delay setpoint, run a probe XCORR scan, validate, and accumulate.

    The delay stage is the dominant central-frequency knob (D19), so sweeping it open-loop
    approximates stepping the centrifuge's central frequency. After each pass we validate the
    peak XCORR against `min_xcorr` and log a warning on a weak pass (a hook for retake logic).

    NOTE: full ν_start/ν_end control (grating chirp-rate + truncation) needs the M4 PID loops;
    until then this sweeps the delay stage only. All rows are tagged with `delay_mm`.
    """
    total = len(delay_setpoints_mm)
    for i, delay_mm in enumerate(delay_setpoints_mm):
        lab.checkpoint()  # cancellation point between passes
        lab.log(f"setpoint {i + 1}/{total}: delay = {delay_mm} mm")
        lab.delay.move_to(delay_mm)

        first_row = len(lab.records)
        for position_mm in lab.frange(start_mm, stop_mm, step_mm):
            lab.probe.move_to(position_mm)
            lab.record(delay_mm=delay_mm, probe_mm=position_mm, xcorr=lab.xcorr_point())

        pass_rows = lab.records[first_row:]
        peak = max((row["xcorr"] for row in pass_rows), default=0.0)
        if peak < min_xcorr:
            lab.log(f"  WARNING: weak pass at delay {delay_mm} mm (peak {peak:.4g} < {min_xcorr})")

    return lab.save(save_path)
