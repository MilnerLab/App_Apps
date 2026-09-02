"""Replay recorded spectra through the slow (frozen-template) loop, headless.

Answers the question the panel cannot: when the capture counter sits at 0/10, is the
loop rejecting every trace, silently abandoning a completed run, or capturing fine and
simply never saying so?

It drives the REAL TemplateTracker over the REAL spectra in an xcorr .h5, at a fixed
delay/grating setpoint so the fringe shape is genuinely static, and prints per-frame what
the tracker did and per-run why a capture ended.

    python tools/replay_stabilization.py xcorr_runs/XCORR_20260818_204942.h5
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

import h5py
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base_core.quantities.enums import Prefix

from app_apps.analysis.phase_control.subprocess.domain.fringe_visibility import (
    fringe_visibility,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    FringeFitParams,
    StabilizationConfig,
)
from app_apps.analysis.phase_control.subprocess.domain.template_tracker import (
    TemplateState,
    TemplateTracker,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="path to an XCORR_*.h5")
    ap.add_argument("--frames", type=int, default=40, help="how many spectra to replay")
    ap.add_argument("--verbose", action="store_true", help="show the fit's own INFO lines")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="    | %(name)s %(levelname)s %(message)s")

    with h5py.File(args.run, "r") as f:
        wl = np.asarray(f["spectra/wavelength_nm"], float)
        delay = np.asarray(f["spectra/delay_mm"], float)
        grating = np.asarray(f["spectra/grating_mm"], float)
        # A stabilization loop sees ONE setpoint. Replaying across a scan would change the
        # fringe shape under the tracker and invalidate honestly, which is not the question.
        key = (delay[0], grating[0])
        idx = np.flatnonzero((delay == key[0]) & (grating == key[1]))[: args.frames]
        counts = np.asarray(f["spectra/counts"][idx.tolist()], float)

    print(f"{args.run}")
    print(f"  {len(idx)} consecutive spectra at delay={key[0]:.4f} mm grating={key[1]:.4f} mm")

    cfg = StabilizationConfig(params=FringeFitParams())
    print(f"  window {cfg.wavelength_range.min.value(Prefix.NANO):.1f}-"
          f"{cfg.wavelength_range.max.value(Prefix.NANO):.1f} nm | "
          f"min_visibility {cfg.min_visibility:.3f} | "
          f"rms_frac < {cfg.rms_frac_threshold:.2f} | inliers > {cfg.inlier_threshold:.0f}%")

    tracker = TemplateTracker(cfg)
    tracker.request_capture()

    lo = cfg.wavelength_range.min.value(Prefix.NANO)
    hi = cfg.wavelength_range.max.value(Prefix.NANO)
    mask = (wl >= lo) & (wl <= hi)
    print(f"  {int(mask.sum())} of {wl.size} pixels fall inside that window")
    print()

    last = tracker.capture_progress[0]
    locked_at = None
    for i, inten in enumerate(counts):
        vis = fringe_visibility(inten[mask])
        before = tracker.state
        out = tracker.update(wl, inten)
        got, need = tracker.capture_progress
        note = ""
        if before is TemplateState.CAPTURING and got == 0 and last > 0:
            note = "  <-- run BROKEN, back to 0" if out.state is TemplateState.CAPTURING \
                   and not out.template_changed else "  <-- run finished"
        if out.template_changed:
            note = "  <-- TEMPLATE INSTALLED"
            locked_at = i
        print(f"  frame {i:>3}  vis={vis:6.3f}  accepted={out.committed!s:<5} "
              f"{tracker.state.value:<9} {got}/{need}{note}")
        last = got
        if locked_at is not None:
            break

    print()
    if locked_at is not None:
        print(f"VERDICT: the loop locks after {locked_at + 1} frames. Capture works.")
    elif tracker.capture_progress[0] == 0:
        print("VERDICT: every trace was rejected -- the counter can never leave 0/10.")
    else:
        print(f"VERDICT: capture stalled part-way at {tracker.capture_progress[0]}/10 "
              f"within {len(counts)} frames.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
