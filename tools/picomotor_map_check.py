"""Verify the motor→mirror mapping against hardware, one axis at a time.

The mapping in ``PicomotorConfig.DEFAULT_MIRRORS`` (motors 1-2 on one mirror; motor 3
the critical yaw, motor 4 pitch on the same mirror as 3) is what the operator
reported and has never been checked. Software cannot check it — the evidence is
which beam moves on which screen — so this drives each axis in isolation and asks
the person at the table what happened.

It is deliberately conservative:

* one axis at a time, out and straight back, so a session's alignment survives the
  check even if you abandon it half way;
* the default increment is large enough to see (50) and the return move is the exact
  negative, though on an open-loop stage backlash means "back" is only approximate —
  the counter is restored exactly, the mirror only nearly;
* it prints the counters before and after each axis so any un-restored offset is
  visible rather than silent;
* nothing is written to config. The output is for a human to read and then edit
  ``DEFAULT_MIRRORS`` deliberately.

Run with the app closed (WinUSB is exclusive, and so is LabVIEW):

    .venv/Scripts/python.exe tools/picomotor_map_check.py [--steps 50] [--axes 1,2,3,4]
    .venv/Scripts/python.exe tools/picomotor_map_check.py --dry-run     # no motion
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_readout.picomotor.config import DEFAULT_MIRRORS, PicomotorConfig  # noqa: E402
from control_readout.picomotor.picomotor_driver import Picomotor8742  # noqa: E402

#: Pause after a move before asking, so the operator is looking at a settled beam.
SETTLE_S = 1.0


def _report_mapping() -> None:
    print("Mapping currently assumed (UNVERIFIED):")
    for m in DEFAULT_MIRRORS:
        flag = "   <-- marked CRITICAL" if m.critical else ""
        print(f"  {m.name}: yaw=motor {m.yaw_axis}, pitch=motor {m.pitch_axis}{flag}")
    print()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--steps", type=int, default=50,
                    help="steps to move out and back per axis (default 50)")
    ap.add_argument("--axes", default="1,2,3,4", help="comma-separated axes to test")
    ap.add_argument("--dry-run", action="store_true",
                    help="connect and read counters, move nothing")
    ap.add_argument("--conn", default=None, help="USB index or IP (default: config)")
    args = ap.parse_args(argv[1:])

    axes = [int(a) for a in args.axes.split(",") if a.strip()]
    _report_mapping()

    cfg = PicomotorConfig.from_env(mock=False)
    if args.conn:
        cfg.host = args.conn
        cfg.transport = "network" if "." in args.conn else "usb"

    driver = Picomotor8742(cfg)
    driver.open()
    try:
        before = {a: driver.position(a) for a in axes}
        print("counters before:", before)
        if args.dry_run:
            print("\n--dry-run: nothing moved.")
            return 0

        print(f"\nMoving each axis +{args.steps} then -{args.steps}. "
              f"Watch which mirror/beam responds.\n")
        findings: dict[int, str] = {}
        for axis in axes:
            input(f"  axis {axis}: press Enter to move (Ctrl-C to stop)... ")
            driver.move_by(axis, args.steps)
            _wait_idle(driver, axis)
            time.sleep(SETTLE_S)
            answer = input(f"  axis {axis}: what moved, and which way? ").strip()
            driver.move_by(axis, -args.steps)
            _wait_idle(driver, axis)
            after = driver.position(axis)
            restored = "OK" if after == before[axis] else f"NOT RESTORED ({after})"
            print(f"  axis {axis}: counter {restored}\n")
            findings[axis] = answer

        print("\n=== observed mapping ===")
        for axis in axes:
            print(f"  motor {axis}: {findings.get(axis, '(no answer)')}")
        print("\ncounters after:", {a: driver.position(a) for a in axes})
        print("\nIf this contradicts DEFAULT_MIRRORS, edit it in "
              "Devices/control_readout/picomotor/config.py and say so in the handoff.")
        return 0
    finally:
        driver.close()
        print("controller closed")


def _wait_idle(driver: Picomotor8742, axis: int, timeout_s: float = 30.0) -> None:
    """Block until the axis stops. The 8742 returns from move_by immediately."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not driver.is_moving(axis):
            return
        time.sleep(0.05)
    print(f"  WARNING: axis {axis} still reports moving after {timeout_s:.0f}s")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
