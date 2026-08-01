"""SINGLE-AXIS motion soak — exercise the real move+poll load on ONE stage only.

This is the closest proxy to a full run's stressor *without* running a full scan or moving
all three stages: it repeatedly issues small **round-trip** relative moves (PR +A then PR -A)
on ONE axis, each followed by wait_for_motion's MD? polling — the exact PA/PR-write +
MD?-read pattern a scan uses. Moves are symmetric so the axis returns to where it started;
its homing reference and alignment are preserved (net displacement ~0).

    App_Apps\\.venv\\Scripts\\python.exe App_Apps-xcorr\\tools\\esp301_single_axis_soak.py \\
        --axis 3 --amplitude 0.5 --minutes 10

SAFETY:
  * It DOES command real motion on the chosen axis (PR). Pick an axis and amplitude that are
    safe to jog: default amplitude is 0.5 mm and it refuses to start if current_pos +/- A
    leaves the --limit-lo/--limit-hi window you pass (pass the axis' real soft limits).
  * It energises only the chosen axis (MO) and never homes (no OR), so it will not move the
    stage to origin.
  * the port is always released in a finally.
  * Verifies each round-trip returns to the start position within --tolerance; a drift or a
    comms fault stops the soak and reports.

What it PROVES / does not: surviving N minutes of real move+poll cycles is strong evidence
the motion comms path is stable for a run of similar length — it is NOT proof (the hard
wedge is undocumented/emergent). One axis is exercised; a full run interleaves three, but
the per-command IO discipline is identical.
"""
from __future__ import annotations

import argparse
import datetime
import time
from pathlib import Path

from control_readout.esp_301.controller import ESP301Controller, ESP301Error

PORT = "COM2"      # RS-232 (2026-07-22); was COM7 over USB.
BAUD = 19200       # RS-232 front-panel baud.
AXIS_NAME = {1: "FMS300PP (probe)", 2: "MFA-CC (delay)", 3: "UTS150CC (grating)"}
LOG_PATH = Path(__file__).resolve().parents[2] / "Docs" / "esp301_single_axis_soak.log"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--axis", type=int, required=True, choices=(1, 2, 3), help="axis to jog")
    p.add_argument("--amplitude", type=float, default=0.5, help="round-trip half-amplitude, mm")
    p.add_argument("--minutes", type=float, default=10.0, help="soak duration, minutes")
    p.add_argument("--limit-lo", type=float, required=True, help="axis soft-limit low, mm (safety guard)")
    p.add_argument("--limit-hi", type=float, required=True, help="axis soft-limit high, mm (safety guard)")
    p.add_argument("--tolerance", type=float, default=0.01, help="return-to-start tolerance, mm")
    p.add_argument("--settle", type=float, default=0.05, help="dwell after each move, s")
    args = p.parse_args(argv)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = LOG_PATH.open("a", encoding="utf-8")

    def say(msg: object = "") -> None:
        print(msg)
        out.write(f"{msg}\n")
        out.flush()

    ax = args.axis
    A = args.amplitude
    say("\n" + "=" * 72)
    say(f"ESP301 SINGLE-AXIS soak  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}  "
        f"axis {ax} {AXIS_NAME[ax]}  +/-{A} mm  {args.minutes:.0f} min")
    say("=" * 72)

    esp = ESP301Controller(port=PORT, baud=BAUD, rtscts=True, write_timeout=5.0)
    try:
        esp.connect()
    except Exception as exc:
        say(f"OPEN FAILED on {PORT}: {exc!r} — something else holds the port. Aborting.")
        out.close()
        return 2

    started = time.monotonic()
    deadline = started + args.minutes * 60.0
    cycles = 0
    try:
        te = esp.check_errors()
        start_pos = esp.get_position(ax)
        say(f"start: TE?={te}  axis {ax} TP={start_pos:.4f}  MO?={esp._query(f'{ax}MO?')}")
        if te != 0:
            say(f"latched controller error TE?={te}. Clear it first. Aborting.")
            return 3
        lo = min(start_pos - A, start_pos + A)
        hi = max(start_pos - A, start_pos + A)
        if lo < args.limit_lo or hi > args.limit_hi:
            say(f"REFUSING: round-trip window [{lo:.4f}, {hi:.4f}] exceeds soft limits "
                f"[{args.limit_lo}, {args.limit_hi}]. Reduce --amplitude or fix limits.")
            return 4

        esp.initialize(ax)  # MO — energize (no motion), in case it came up off
        time.sleep(0.2)

        while time.monotonic() < deadline:
            cycles += 1
            esp.move_relative(ax, +A)   # PR +A, blocks on MD? polling
            esp.move_relative(ax, -A)   # PR -A, back to start
            time.sleep(args.settle)
            pos = esp.get_position(ax)
            drift = pos - start_pos
            if abs(drift) > args.tolerance:
                raise ESP301Error(f"axis {ax} did not return to start: {pos:.4f} vs "
                                  f"{start_pos:.4f} (drift {drift:+.4f} mm) at cycle {cycles}")
            te = esp.check_errors()
            if te != 0:
                raise ESP301Error(f"TE?={te} at cycle {cycles}")
            if cycles % 20 == 0:
                elapsed = (time.monotonic() - started) / 60.0
                say(f"  [{elapsed:5.1f} min] {cycles} round-trips; TP={pos:.4f} "
                    f"drift={drift:+.4f}  TE?={te}")

        elapsed = (time.monotonic() - started) / 60.0
        final = esp.get_position(ax)
        say(f"\nSURVIVED: {elapsed:.1f} min, {cycles} round-trips on axis {ax}. "
            f"Final TP={final:.4f} (start {start_pos:.4f}). TE?={esp.check_errors()}")
        say("=> motion comms path stable for a run of similar length on this axis. "
            "(Not proof; one axis exercised.)")
        return 0
    except ESP301Error as exc:
        elapsed = (time.monotonic() - started) / 60.0
        say(f"\nFAULT after {elapsed:.1f} min, {cycles} round-trips: {exc}")
        say("=> if it went silent on RS-232 (COM2), check the cable / front-panel baud / a "
            "latched fault (an overflow self-drains). (Over the old USB path: re-enumerate.)")
        return 1
    except KeyboardInterrupt:
        say("\ninterrupted by operator — the axis may be mid-move; it will stop at the "
            "current target. Re-check position before the next run.")
        return 130
    finally:
        try:
            esp.disconnect()
            say(f"{PORT} released.")
        except Exception as exc:  # pragma: no cover
            say(f"WARNING: disconnect raised {exc!r}; {PORT} may still be held.")
        out.close()


if __name__ == "__main__":
    raise SystemExit(main())
