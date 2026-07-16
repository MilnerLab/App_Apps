"""Cold-fit tests: every ``analyze_trace`` call is a fresh, seed-independent fit —
there is NO warm-starting. Two independent fits of the same trace must be bit-identical
(no hidden state carried between calls), and a cold fit must succeed on the real traces.

Run directly:  python test/fringe_seed_test.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import (  # noqa: E402
    FitTunables, analyze_trace,
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


def test_cold_fit_is_seed_independent() -> None:
    """Fitting the same trace twice, each from scratch, must give the SAME optimum to
    the bit — the fit carries no state between calls, so no previous frame can bias it."""
    t = FitTunables()
    for name in FILES:
        x, y = _load(name)
        a = analyze_trace(x, y, t)
        b = analyze_trace(x, y, t)
        assert a.accepted and b.accepted, f"{name}: cold fit rejected"
        assert a.l0 == b.l0, f"{name}: l0 not reproducible ({a.l0} vs {b.l0})"
        for i in range(4):
            assert a.csig[i] == b.csig[i], f"{name}: c{i} not reproducible"
        assert a.phase_at(LAMBDA_REF) == b.phase_at(LAMBDA_REF), \
            f"{name}: phase_ref not reproducible"
        print(f"OK  cold==cold  {name:24s} l0={a.l0:.3f} "
              f"phi_ref={a.phase_at(LAMBDA_REF):.4f}")


def test_analyze_trace_takes_no_seed() -> None:
    """The warm-start entry point is gone: analyze_trace must not accept a ``seed``."""
    import inspect
    params = inspect.signature(analyze_trace).parameters
    assert "seed" not in params, "analyze_trace still exposes a warm-start seed parameter"
    print("OK  analyze_trace has no seed parameter (warm-starting removed)")


if __name__ == "__main__":
    test_cold_fit_is_seed_independent()
    test_analyze_trace_takes_no_seed()
    print("\nAll cold-fit tests passed.")
