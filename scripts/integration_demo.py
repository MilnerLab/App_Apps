#!/usr/bin/env python3
"""Interpretable closed-loop integration demo (no pytest needed).

Runs each control loop against the in-process OpticalPlant through the full real chain
(spectrometer -> shared memory -> lmfit fit -> PID -> routine -> actuation) and prints a
step-by-step trace table + summary for each, plus a CSV and a convergence PNG per loop.

    .venv312/Scripts/python.exe scripts/integration_demo.py [--out DIR]

Shows three things clearly:
  * delay -> nu0 and truncation -> nu_end close cleanly (envelope observables).
  * HWP -> phase0 closes when the fit is warm-started (the packaged `lock_phase` fits cold and
    can't recover phase0 from a fringed spectrum -- see test/integration/test_closed_loop.py).
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "test", "integration"))

from app_apps.analysis.spectrum_info.model import SpectrumParams  # noqa: E402
from app_apps.routines.linear.scripts.control_loops import (  # noqa: E402
    lock_central_frequency,
    lock_terminal_frequency,
)
from base_core.framework.events.event_bus import EventBus  # noqa: E402
from optical_plant import OpticalPlant, build_plant_lab, warm_phase_lock  # noqa: E402
from report import format_trace, plot_convergence, write_csv  # noqa: E402


def _run(plant: OpticalPlant, fn):
    lab, _cancel = build_plant_lab(plant)
    plant.start()
    try:
        return fn(lab)
    finally:
        lab.close()
        plant.close()


def demo_central_frequency(out: str) -> None:
    plant = OpticalPlant(EventBus(), nu0_base=374.0, nu0_slope=3.0)
    target = 380.0
    result = _run(plant, lambda lab: lock_central_frequency(
        lab, target_thz=target, kp=0.15, tolerance_thz=0.05, max_iterations=80, dt_s=0.05))
    print(format_trace(
        "Closed loop: lock_central_frequency (delay -> nu0)",
        "nu0[THz] = 374.00 + 3.00*delay_pos[mm]",
        plant, "nu0", target=target, tolerance=0.05, command_unit="mm",
        result=result, final_true=plant.state().nu0_thz))
    write_csv(plant, "nu0", os.path.join(out, "central_frequency.csv"), target=target)
    plot_convergence(plant, "nu0", os.path.join(out, "central_frequency.png"), target=target)
    print()


def demo_terminal_frequency(out: str) -> None:
    plant = OpticalPlant(EventBus(), nu0_base=374.0, nu0_slope=3.0, bw_base=30.0, bw_slope=-4.0)
    target = plant.state().nu_end_thz + 2.5  # state() needs no lab/producer
    result = _run(plant, lambda lab: lock_terminal_frequency(
        lab, target_thz=target, kp=0.3, tolerance_thz=0.05, max_iterations=80, dt_s=0.05))
    print(format_trace(
        "Closed loop: lock_terminal_frequency (truncation -> nu_end)",
        "nu_end[THz] rises as truncation increases (bandwidth shrinks)",
        plant, "nu_end", target=target, tolerance=0.05, command_unit="mm",
        result=result, final_true=plant.state().nu_end_thz))
    write_csv(plant, "nu_end", os.path.join(out, "terminal_frequency.csv"), target=target)
    plot_convergence(plant, "nu_end", os.path.join(out, "terminal_frequency.png"), target=target)
    print()


def demo_phase(out: str) -> None:
    plant = OpticalPlant(EventBus(), tau_ps=0.15, phase_off=0.05, phase_gain=1.0)
    target = 0.80
    fit_init = SpectrumParams(
        central_wavelength_nm=801.6, bandwidth_nm=30.0, amp_upper=1.0, amp_lower=0.05,
        phase0=0.0, tau_ps=0.15, g2=0.0, g3=0.0)
    result = _run(plant, lambda lab: warm_phase_lock(
        lab, target_rad=target, fit_init=fit_init, kp=0.5))
    print(format_trace(
        "Closed loop: HWP -> phase0 (warm-started fit)",
        "phase0[rad] = 0.05 + 1.00*hwp_angle[rad];  fit warm-started each step",
        plant, "phase0", target=target, tolerance=0.02, command_unit="rad",
        result=result, final_true=plant.state().phase0))
    write_csv(plant, "phase0", os.path.join(out, "phase.csv"), target=target)
    plot_convergence(plant, "phase0", os.path.join(out, "phase.png"), target=target)
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "integration_demo"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    print(f"writing CSV + PNG artifacts to: {args.out}\n")
    demo_central_frequency(args.out)
    demo_terminal_frequency(args.out)
    demo_phase(args.out)
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
