"""Human-readable reporting for the closed-loop demo: trace table + CSV + convergence plot.

A control loop drives the plant; the plant logs every actuation with the resulting *true*
observable (`OpticalPlant.actuations`). These helpers turn that log into an interpretable
step-by-step table, a CSV, and a PNG. The loop's own (measured) convergence comes from the
returned `LockResult`; the plant's true state is the ground truth the loop drove it to.
"""
from __future__ import annotations

import csv
from typing import Callable, Optional

from app_apps.control.lock import LockResult
from optical_plant import OpticalPlant, PlantState

# observable name -> (getter, pretty label, unit)
_OBSERVABLES: dict[str, tuple[Callable[[PlantState], float], str, str]] = {
    "nu0": (lambda s: s.nu0_thz, "nu0", "THz"),
    "nu_end": (lambda s: s.nu_end_thz, "nu_end", "THz"),
    "phase0": (lambda s: s.phase0, "phase0", "rad"),
}


def _rows(plant: OpticalPlant, observable: str) -> list[tuple[int, float, float]]:
    """(step, command, true_observable) per actuation, in order."""
    getter = _OBSERVABLES[observable][0]
    return [(i + 1, cmd, getter(state)) for i, (_t, _k, cmd, state) in enumerate(plant.actuations)]


def format_trace(
    title: str,
    plant_desc: str,
    plant: OpticalPlant,
    observable: str,
    *,
    target: float,
    tolerance: float,
    command_unit: str,
    result: LockResult,
    final_true: float,
) -> str:
    getter, label, unit = _OBSERVABLES[observable]
    rows = _rows(plant, observable)
    lines = [
        f"=== {title} ===",
        f"plant: {plant_desc}",
        f"target {label} = {target:.4f} {unit}   tolerance = {tolerance:g} {unit}   "
        f"({len(rows)} actuations)",
        "",
        f" step | command ({command_unit:>3}) | {label:>9} ({unit}) |  error",
        "------+----------------+----------------+----------",
    ]
    for step, cmd, value in rows:
        lines.append(f" {step:4d} |    {cmd:+8.4f}    |   {value:11.4f}  | {target - value:+8.4f}")
    verdict = "CONVERGED" if result.converged else "DID NOT CONVERGE"
    lines += [
        "",
        f" -> {verdict} in {result.iterations} steps: "
        f"measured {label} = {result.final_value:.4f} {unit} "
        f"(err {abs(result.final_error):.4f} {'<=' if result.converged else '>'} {tolerance:g}); "
        f"true {label} = {final_true:.4f} {unit}",
    ]
    return "\n".join(lines)


def write_csv(plant: OpticalPlant, observable: str, path: str, *, target: float) -> str:
    label = _OBSERVABLES[observable][1]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["step", "command", label, "error"])
        for step, cmd, value in _rows(plant, observable):
            w.writerow([step, f"{cmd:.6f}", f"{value:.6f}", f"{target - value:.6f}"])
    return path


def plot_convergence(
    plant: OpticalPlant, observable: str, path: str, *, target: float
) -> Optional[str]:
    """Save a PNG of the true observable vs step with the target line. Returns path or None."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    _getter, label, unit = _OBSERVABLES[observable]
    rows = _rows(plant, observable)
    steps = [r[0] for r in rows]
    values = [r[2] for r in rows]
    fig, ax = plt.subplots()
    ax.plot(steps, values, marker="o", label=f"true {label}")
    ax.axhline(target, color="r", ls="--", label="target")
    ax.set_xlabel("step")
    ax.set_ylabel(f"{label} ({unit})")
    ax.set_title(f"closed-loop convergence: {label}")
    ax.legend()
    fig.savefig(path)
    plt.close(fig)
    return path
