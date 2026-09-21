"""READ-ONLY endurance soak — reproduce a run's COMMS profile with NO motion.

This is the strongest test we can run *without writing to the stages*. It never sends
PA/PR/MO/MF/OR/VA/ST — only status queries (VE?, nTP, nMD?, TE?) — so it cannot move a
stage or change a setting, yet it drives the exact IO discipline a real scan uses
(read-after-write through ESP301Controller, one-write-one-drain) and interleaves the same
kind of idle gaps a scan has between setpoints/acquisitions. Two things it can catch that
a quick link-check cannot:

  * a slow comms degradation / wedge under sustained real-cadence polling, and
  * the *idle* re-wedge (USB selective-suspend / power management) — the failure that
    actually recurred in the field, which only shows up when the port is held over time.

It holds the port for the whole soak and frees it in a finally. Run it when the port is free
and (ideally) an operator is present, since the point is to provoke a wedge if one is
latent. It appends a transcript to Docs/esp301_readonly_soak.log.

    App_Apps\\.venv\\Scripts\\python.exe App_Apps-xcorr\\tools\\esp301_readonly_soak.py --minutes 20

What it PROVES / does not: surviving a long soak is strong empirical evidence the comms
path is stable and the idle-wedge is not firing — it is NOT proof the multi-hour run is
wedge-proof (the hard wedge is an undocumented emergent bridge state; motion is not
exercised here). Pair it with the single-axis soak for the motion path.
"""
from __future__ import annotations

import argparse
import datetime
import time
from pathlib import Path

from control_readout.esp_301.controller import ESP301Controller, ESP301Error

PORT = "COM2"      # RS-232 (2026-07-22); was COM7 over USB.
BAUD = 19200       # RS-232 front-panel baud; was 921600 over USB.
AXES = (1, 2, 3)
LOG_PATH = Path(__file__).resolve().parents[2] / "Docs" / "esp301_readonly_soak.log"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--minutes", type=float, default=20.0, help="soak duration, minutes")
    p.add_argument("--poll-hz", type=float, default=10.0, help="MD?/TP poll rate in the active bursts")
    p.add_argument("--burst-s", type=float, default=3.0, help="active-poll seconds per cycle (mimics a move)")
    p.add_argument("--idle-s", type=float, default=2.0, help="quiet seconds per cycle (mimics acquisition/settle)")
    args = p.parse_args(argv)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = LOG_PATH.open("a", encoding="utf-8")

    def say(msg: object = "") -> None:
        print(msg)
        out.write(f"{msg}\n")
        out.flush()

    say("\n" + "=" * 72)
    say(f"ESP301 READ-ONLY soak  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}  "
        f"({args.minutes:.0f} min, {args.poll_hz:.0f} Hz bursts)")
    say("=" * 72)

    esp = ESP301Controller(port=PORT, baud=BAUD, rtscts=True, write_timeout=5.0)
    try:
        esp.connect()
    except Exception as exc:
        say(f"OPEN FAILED on {PORT}: {exc!r} — something else holds the port. Aborting.")
        out.close()
        return 2

    n_queries = 0
    n_empty = 0
    started = time.monotonic()
    deadline = started + args.minutes * 60.0
    period = 1.0 / args.poll_hz
    cycle = 0

    def q(cmd: str) -> str:
        nonlocal n_queries, n_empty
        r = esp._query(cmd)
        n_queries += 1
        if not r:
            n_empty += 1
        return r

    try:
        ve = q("VE?")
        say(f"start: VE?={ve!r}  TE?={q('TE?')!r}  "
            f"TP=({q('1TP')}, {q('2TP')}, {q('3TP')})")
        if not ve:
            say("no VE? reply at start — link not healthy. Aborting.")
            return 1

        while time.monotonic() < deadline:
            cycle += 1
            # active burst: poll MD?/TP like wait_for_motion does during a move
            t_end = time.monotonic() + args.burst_s
            while time.monotonic() < t_end:
                for ax in AXES:
                    if not q(f"{ax}MD?"):
                        raise ESP301Error(f"empty MD? on axis {ax} at cycle {cycle} "
                                          f"({(time.monotonic()-started)/60:.1f} min in)")
                q("1TP")
                time.sleep(period)
            # quiet gap: port held open but idle (this is where power-mgmt wedges bite)
            time.sleep(args.idle_s)
            # heartbeat health check after the idle gap
            te = q("TE?")
            elapsed = (time.monotonic() - started) / 60.0
            if te == "" :
                raise ESP301Error(f"empty TE? after idle gap, cycle {cycle} ({elapsed:.1f} min)")
            if cycle % 10 == 0:
                say(f"  [{elapsed:5.1f} min] cycle {cycle}: {n_queries} queries, "
                    f"{n_empty} empty; TE?={te}  TP1={q('1TP')}")

        elapsed = (time.monotonic() - started) / 60.0
        say(f"\nSURVIVED: {elapsed:.1f} min, {cycle} cycles, {n_queries} queries, "
            f"{n_empty} empty replies. Final VE?={q('VE?')!r} TE?={q('TE?')!r}")
        say("=> comms path stable under sustained real-cadence polling + idle gaps. "
            "(Not proof the full motion run is wedge-proof; run the single-axis soak too.)")
        return 0
    except ESP301Error as exc:
        elapsed = (time.monotonic() - started) / 60.0
        say(f"\nWEDGE/FAULT after {elapsed:.1f} min, {cycle} cycles, {n_queries} queries: {exc}")
        say("=> the link stopped answering with NO motion involved. On RS-232 (COM2) an "
            "overflow self-drains, so a persistent silence points at the cable / front-panel "
            "baud / a latched fault — check those. (Over the old USB path this needed a "
            "re-enumeration.)")
        return 1
    except KeyboardInterrupt:
        say("\ninterrupted by operator.")
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
