"""Parity check: the ported ``fringe_fit.analyze_trace`` must reproduce the
standalone ``Data/20260709/spectrometer/plot_traces.py`` numbers on the three
real traces it was validated against.

Run directly:  python test/fringe_fit_parity_test.py
(uses only numpy/scipy/pandas + the pure fringe_fit module — no app/base_core.)
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import (  # noqa: E402
    FitTunables, analyze_trace, gauss,
)

DATA_DIR = r"D:\Documents\University\UBC research\2026\Data\20260709\spectrometer"
ZOOM = (790.0, 814.0)

# Ground truth printed by plot_traces.py (2026-07-14 run).
#   file -> (mu_upper, fwhm_upper, l0, has_null, inlier_pct, rms_sig, c3)
EXPECTED = {
    "da17_1GA_-75.xls":       (801.96, 8.34, 799.48, True,  100.0, 1.1, 0.000148),
    "da_15.95ga_-55.29.xls":  (802.15, 9.08, 791.10, False, 97.0,  1.0, -0.00385),
    "da_15.95ga_-75.xls":     (801.91, 9.20, 792.35, False, 95.0,  0.8, -0.00336),
}


def _load(path: str) -> tuple[np.ndarray, np.ndarray]:
    with open(path) as fh:
        lines = fh.readlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("Wavelength")) + 1
    df = pd.read_csv(path, sep="\t", skiprows=start, names=["Wavelength", "Amplitude"])
    d = df[(df.Wavelength >= ZOOM[0]) & (df.Wavelength <= ZOOM[1])]
    return d.Wavelength.values, d.Amplitude.values


def _check(name: str) -> None:
    mu_e, fwhm_e, l0_e, null_e, inl_e, rms_e, c3_e = EXPECTED[name]
    x, y = _load(os.path.join(DATA_DIR, name))
    # Pin trunc_threshold to 0.25: EXPECTED was frozen from the standalone script's
    # 2026-07-14 run at that value. The shipping DEFAULT is now 0.40 (harness-tuned), but
    # this test is a port-parity regression guard against the historical reference numbers,
    # so it must use the trunc they were generated at -- not whatever the default becomes.
    r = analyze_trace(x, y, FitTunables(trunc_threshold=0.25))
    assert r.accepted, f"{name}: fit rejected"

    _, mu, sig, _ = r.pU
    fwhm = 2.3548 * abs(sig)
    c3 = r.csig[3]

    def close(got, exp, tol, label):
        assert abs(got - exp) <= tol, f"{name}: {label} {got:.5g} vs {exp:.5g} (tol {tol})"

    close(mu, mu_e, 0.05, "upper mu")
    close(fwhm, fwhm_e, 0.05, "upper FWHM")
    close(r.l0, l0_e, 0.15, "l0")
    assert r.has_null == null_e, f"{name}: has_null {r.has_null} vs {null_e}"
    close(r.inlier_pct, inl_e, 3.0, "inlier %")
    close(r.rms_sig, rms_e, 0.3, "signal rms")
    close(c3, c3_e, max(1e-5, 0.05 * abs(c3_e)), "c3 (TOD)")
    print(f"OK  {name:24s} mu={mu:.2f} fwhm={fwhm:.2f} l0={r.l0:.2f} "
          f"null={r.has_null} inl={r.inlier_pct:.0f}% rms={r.rms_sig:.1f} c3={c3:.3g}")


def test_parity() -> None:
    for name in EXPECTED:
        _check(name)


if __name__ == "__main__":
    test_parity()
    print("\nAll parity checks passed.")
