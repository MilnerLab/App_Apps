"""RF frequency-range readout: correct conversion, signed reporting, formatting.

Self-contained -- imports only the app's fringe_core, no external data. Signals failure by
raising (assert), so it runs under pytest or directly:  python test/rf_range_test.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app_apps.analysis.phase_control.subprocess.domain import fringe_core as core  # noqa: E402


def test_rf_range_readout() -> None:
    """The GHz overlay: conversion, SIGNED range through a null, and the formatting rules."""
    # 28.125 GHz per cycle/nm, from 9 nm ~ 320 ps assumed linear.
    ok = abs(core.GHZ_PER_CYC_PER_NM - 28.125) < 1e-9
    print(f"\n{'PASS' if ok else 'FAIL'}  9nm/320ps => {core.GHZ_PER_CYC_PER_NM} GHz per cycle/nm")

    # A pure carrier with no chirp is ONE frequency across the whole band.
    c1 = 2.0 * np.pi * 1.0                     # exactly 1 cycle/nm
    lo, hi = core.rf_range_ghz((0.0, c1, 0.0, 0.0), 802.0)
    ok2 = abs(lo - 28.125) < 1e-6 and abs(hi - 28.125) < 1e-6
    print(f"{'PASS' if ok2 else 'FAIL'}  1 cycle/nm, no chirp -> {lo:.3f}-{hi:.3f} GHz "
          f"(flat at 28.125)")

    # With a chirp the frequency sweeps LINEARLY, crosses zero at the null, then goes
    # NEGATIVE past it. The range is SIGNED, so it must show that negative excursion -- not
    # fold it back to a spurious ~0 with abs(). c1 + 2*c2*u = 0 at u = -c1/(2*c2) = 3 nm;
    # out to +9 nm f reaches -2 cycles/nm (-56.25 GHz), and to -9 nm +4 cycles/nm (112.5).
    c2 = -c1 / (2 * 3.0)
    lo2, hi2 = core.rf_range_ghz((0.0, c1, c2, 0.0), 802.0)
    ok3 = lo2 < -50.0 and hi2 > 100.0          # genuinely negative past the null, not ~0
    print(f"{'PASS' if ok3 else 'FAIL'}  chirped through a null -> {lo2:.1f} to {hi2:.1f} "
          f"GHz (min goes negative past the null, not abs-folded to ~0)")

    cases = [(100.0, "100"), (20.4, "20"), (9.96, "10"), (1.52, "1.5"),
             (0.44, "0.44"), (0.037, "0.037"), (-19.0, "-19")]
    ok4 = all(core.format_ghz(v) == want for v, want in cases)
    print(f"{'PASS' if ok4 else 'FAIL'}  format_ghz: "
          + ", ".join(f"{v}->{core.format_ghz(v)}" for v, _ in cases)
          + "  (nearest GHz, never under 2 sig figs, signed)")

    ok5 = core.format_rf_range(12.0, 47.0, True) == "12-47 GHz"
    ok5 &= core.format_rf_range(28.125, 28.125, True) == "28 GHz"      # collapses
    ok5 &= core.format_rf_range(-19.0, 30.0, True) == "-19 to 30 GHz"  # negative -> " to "
    ok5 &= "unverified" in core.format_rf_range(12.0, 47.0, False)
    print(f"{'PASS' if ok5 else 'FAIL'}  format_rf_range: "
          f"{core.format_rf_range(12.0, 47.0, True)!r} / "
          f"{core.format_rf_range(28.125, 28.125, True)!r} / "
          f"{core.format_rf_range(-19.0, 30.0, True)!r} / "
          f"{core.format_rf_range(12.0, 47.0, False)!r}")
    assert ok and ok2 and ok3 and ok4 and ok5


if __name__ == "__main__":
    test_rf_range_readout()
    print("\nRF RANGE READOUT: PASS")
