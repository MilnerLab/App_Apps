"""PR2 tests: the warm path reproduces the cold fit on a stable trace, and the
SeedController warm/cold latch behaves per contract.

Run directly:  python test/fringe_seed_test.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import (  # noqa: E402
    FitTunables, FringeFitResult, SeedController, analyze_trace, rejected,
)

DATA_DIR = r"D:\Documents\University\UBC research\2026\Data\20260709\spectrometer"
ZOOM = (790.0, 814.0)
FILES = ["da17_1GA_-75.xls", "da_15.95ga_-55.29.xls", "da_15.95ga_-75.xls"]
LAMBDA_REF = 802.0


def _load(name: str) -> tuple[np.ndarray, np.ndarray]:
    path = os.path.join(DATA_DIR, name)
    with open(path) as fh:
        lines = fh.readlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("Wavelength")) + 1
    df = pd.read_csv(path, sep="\t", skiprows=start, names=["Wavelength", "Amplitude"])
    d = df[(df.Wavelength >= ZOOM[0]) & (df.Wavelength <= ZOOM[1])]
    return d.Wavelength.values, d.Amplitude.values


def test_warm_matches_cold() -> None:
    """Warm-starting from a good fit of the same trace must land on the same
    optimum (and skip the null search) — so the per-shot warm path is stable."""
    t = FitTunables()
    for name in FILES:
        x, y = _load(name)
        cold = analyze_trace(x, y, t, seed=None)
        assert cold.accepted
        warm = analyze_trace(x, y, t, seed=cold)
        assert warm.accepted
        # Warm re-optimizes from the seed, so it lands on the same optimum to
        # numerical noise (measured drift: l0 ~1e-5 nm, phase_ref ~1e-5 rad).
        assert abs(warm.l0 - cold.l0) < 1e-3, f"{name}: warm l0 drifted"
        for i in range(4):
            assert abs(warm.csig[i] - cold.csig[i]) <= max(1e-6, 1e-2 * abs(cold.csig[i])), \
                f"{name}: warm c{i} drifted"
        assert abs(warm.phase_at(LAMBDA_REF) - cold.phase_at(LAMBDA_REF)) < 1e-3, \
            f"{name}: warm phase_ref drifted"
        print(f"OK  warm==cold  {name:24s} l0={warm.l0:.3f} "
              f"phi_ref={warm.phase_at(LAMBDA_REF):.4f}")


def _good() -> FringeFitResult:
    return FringeFitResult(True, (1, 0, 1, 0), (1, 0, 1, 0), 800.0,
                           (0.1, 0.0, 0.0, 0.0), 0.1, 1.0, 99.0, False)


def test_seed_controller_latch() -> None:
    sc = SeedController(redo_after_bad=3)
    # No seed yet -> cold.
    assert sc.next_seed() is None

    # A good fit becomes the warm seed.
    g = _good()
    sc.record(g, good=True)
    assert sc.next_seed() is g and not sc.forcing_cold

    # Bad fits accumulate but do NOT overwrite the seed until the latch trips.
    sc.record(rejected(), good=False)
    sc.record(rejected(), good=False)
    assert sc.next_seed() is g and not sc.forcing_cold and sc.consecutive_bad == 2

    # Third consecutive bad trips forced-cold.
    sc.record(rejected(), good=False)
    assert sc.forcing_cold and sc.next_seed() is None

    # A good (necessarily cold) fit clears the latch and reseeds.
    g2 = _good()
    sc.record(g2, good=True)
    assert not sc.forcing_cold and sc.next_seed() is g2 and sc.consecutive_bad == 0

    # A single good fit resets a partial bad streak.
    sc.record(rejected(), good=False)
    assert sc.consecutive_bad == 1
    sc.record(_good(), good=True)
    assert sc.consecutive_bad == 0 and not sc.forcing_cold
    print("OK  SeedController latch: forces cold after N bad, clears on good")


def test_seed_controller_reset() -> None:
    sc = SeedController(redo_after_bad=2)
    sc.record(_good(), good=True)
    sc.record(rejected(), good=False)
    sc.reset()
    assert sc.next_seed() is None and not sc.forcing_cold and sc.consecutive_bad == 0
    print("OK  SeedController reset clears all state")


if __name__ == "__main__":
    test_warm_matches_cold()
    test_seed_controller_latch()
    test_seed_controller_reset()
    print("\nAll PR2 tests passed.")
