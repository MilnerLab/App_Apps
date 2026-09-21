"""Dump every scan's fitted readout for a run file, at full precision.

Exists for one purpose: P1 (the import-freeze fix) and P2 (the plot additions) both
change code on the fit path without intending to change a single fitted number, and
"intending" is not evidence. Run this before the change and after it, diff the two
files, and the claim is either true or visibly false.

Full ``repr`` precision is deliberate — the acceptance criterion is bit-identical
values, and %.6f would hide a change in the last few ulp that a reader would want to
know about.

Usage:
    python tools/xcorr_fit_baseline.py xcorr_runs/XCORR_20260725_142505.h5 [W_ps ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_apps.analysis.xcorr.frequency import DEFAULT_FWHM_PS, fit_sweep  # noqa: E402
from app_apps.analysis.xcorr.run_loader import load_run  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = argv[1]
    windows = [float(a) for a in argv[2:]] or [DEFAULT_FWHM_PS]

    run = load_run(path)
    print(f"# run {run.path.name}  scans={len(run.scans)}  points={run.n_points}")
    for w in windows:
        print(f"# window_ps={w!r}")
        for ls in run.scans:
            if not ls.probe_mm.size:
                print(f"{ls.setpoint_index}\tEMPTY")
                continue
            tr = fit_sweep(ls.probe_mm, ls.v_mean_pos, fwhm_ps=w)
            print(
                f"{ls.setpoint_index}\t{tr.ok}\t{tr.status}\t"
                f"{tr.f_central_ghz!r}\t{tr.f_central_sigma_ghz!r}\t"
                f"{tr.bandwidth_ghz!r}\t{tr.bandwidth_sigma_ghz!r}\t"
                f"{tr.r2_fringe!r}\t{tr.t_mu_ps!r}\t{tr.order}\t{tr.trusted}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
