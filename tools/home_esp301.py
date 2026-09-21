"""Home the three ESP301 linear stages — the power-up sequence the routine will NOT do.

The XcorrRoutine never homes (by design). After a power cycle the ESP301 comes up
**not homed, motors OFF, TP=0** (memory: esp301-power-cycle-state), so the stages must
be homed once, out of band, before the first scan. This is that helper.

    App_Apps\\.venv\\Scripts\\python.exe App_Apps-xcorr\\tools\\home_esp301.py

Safety contract
---------------
* It drives real motion (``MO`` energise, ``OR`` origin search). It is NOT read-only.
  It does **not** send ``PA``/``PR`` — it only homes; the stages end at their origin.
* Every byte goes through ``ESP301Controller`` (``_write``/``_query``: one-write-one-drain
  under the IO lock), never a raw ``serial.write`` — that discipline is what keeps the
  TI-3410 USB bridge from wedging (defect G19/FM-2). No naked port access here.
* The port is **always** released: ``disconnect()`` runs in a ``finally``, so a failed home,
  a comms fault, or Ctrl-C still closes the port cleanly and leaves it free for the app.
* If the port is held by something else (an app still up, an orphaned subprocess), the open
  fails loudly and we stop — we never fight for the port.

It refuses to move if the controller has a latched error (``TE?``) unless ``--force``.
Homing is idempotent: an already-homed axis just re-runs its origin search.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Devices is on the venv path (control_readout imports fine in the main process).
from control_readout.esp_301.controller import ESP301Controller, ESP301Error

PORT = "COM2"      # RS-232 (2026-07-22); was COM7 over USB.
BAUD = 19200       # RS-232 front-panel baud.
AXES = (1, 2, 3)  # 1=FMS300PP (probe), 2=MFA-CC (delay), 3=UTS150CC (grating)
AXIS_NAME = {1: "FMS300PP", 2: "MFA-CC", 3: "UTS150CC"}


def _report_state(esp: ESP301Controller, label: str) -> None:
    print(f"\n--- {label} ---")
    for ax in AXES:
        try:
            mo = esp._query(f"{ax}MO?").strip()
            tp = esp._query(f"{ax}TP").strip()
        except ESP301Error as exc:
            print(f"  axis {ax} ({AXIS_NAME[ax]:8}): query failed: {exc}")
            continue
        print(f"  axis {ax} ({AXIS_NAME[ax]:8}): MO?={mo:>3}  TP={tp}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeout", type=float, default=120.0,
                   help="per-axis home timeout, s (default 120)")
    p.add_argument("--force", action="store_true",
                   help="home even if TE? reports a latched controller error")
    args = p.parse_args(argv)

    esp = ESP301Controller(port=PORT, baud=BAUD, rtscts=True, write_timeout=5.0)
    try:
        esp.connect()
    except Exception as exc:
        print(f"OPEN FAILED on {PORT}: {exc!r}")
        print("-> something else holds the port (app still up? orphaned subprocess?). "
              f"Free {PORT} first; do NOT taskkill /F a holder.")
        return 2

    try:
        te = esp.check_errors()
        print(f"TE? (latched error) = {te}")
        if te != 0 and not args.force:
            print("-> refusing to home with a latched error. Clear it, or pass --force.")
            return 3

        _report_state(esp, "BEFORE (expected post-power-cycle: MO?=0, TP=0)")

        print("\nEnergising motors (MO) on axes 1, 2, 3 ...")
        for ax in AXES:
            esp.initialize(ax)  # MO
        time.sleep(0.2)

        for ax in AXES:
            print(f"\nHoming axis {ax} ({AXIS_NAME[ax]}) — origin search (OR), "
                  f"blocking up to {args.timeout:.0f}s ...")
            t0 = time.monotonic()
            esp.home(ax, timeout=args.timeout)  # OR + wait_for_motion (under the IO lock)
            print(f"  homed in {time.monotonic() - t0:.1f}s; TP={esp.get_position(ax):.4f}")

        _report_state(esp, "AFTER (expect MO?=1, TP at each axis' origin)")

        te = esp.check_errors()
        print(f"\nfinal TE? = {te}")
        print(f"\nDONE — stages homed. {PORT} will be released now; the app can take it.")
        return 0 if te == 0 else 4
    except KeyboardInterrupt:
        print(f"\ninterrupted — releasing {PORT} (a home in flight may be incomplete).")
        return 130
    except ESP301Error as exc:
        print(f"\nESP301 error during homing: {exc}")
        return 5
    finally:
        # ALWAYS free the port, whatever happened above — this is the whole point.
        try:
            esp.disconnect()
            print(f"{PORT} released.")
        except Exception as exc:  # pragma: no cover
            print(f"WARNING: disconnect raised {exc!r}; {PORT} may still be held.")


if __name__ == "__main__":
    raise SystemExit(main())
