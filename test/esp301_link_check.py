"""ESP301 link check — STRICTLY READ-ONLY. Written for defect G19.

Run this when the ESP301 is not responding, or before/after a power cycle, to
find out *what kind* of not-responding it is. It appends a timestamped
transcript to Docs/esp301_g19_recheck.log.

    App_Apps\\.venv\\Scripts\\python.exe App_Apps\\test\\esp301_link_check.py

Safety contract
---------------
This script cannot move a stage or change a setting. It sends only status
queries — ``VE?``, ``TE?``, ``TB?``, ``<n>TP``, ``<n>MD?``, ``<n>MO?``, ``<n>ID?``.
It never sends ``PA``, ``PR``, ``ST``, ``MO``/``MF``, ``OR``, ``VA``, or even a
bare ``CR``. It is safe to run while an experiment is in progress.

Reading the result
------------------
* **Open fails** — something else holds COM7. Not a controller fault; find the
  process.
* **Bytes come back** — the link is healthy. Check ``TE?``/``TB?`` for a latched
  controller error.
* **Zero bytes at both bauds, passive listen silent** — the controller's CPU has
  stopped servicing its serial interface. This is the G19 state. It does not
  self-recover; it needs a power cycle. **Read the front-panel error display
  first — power-cycling destroys it.**

Why raw reads instead of ``readline()``: ``readline()`` waits for a ``\\n`` and
returns ``''`` both when nothing arrived and when bytes arrived without an LF
terminator. Those are different faults and must be told apart. This is also
exactly the ambiguity that made ``ESP301Controller.wait_for_motion`` misreport a
dead link as a stuck axis (defect G20).
"""
from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path

import serial

PORT = "COM7"
#: USB (TI-3410 bridge) rate first, then the front-panel RS-232 default. Testing
#: both is what rules out "something changed the baud" as an explanation.
BAUDS = (921600, 19200)
LOG_PATH = Path(__file__).resolve().parents[2] / "Docs" / "esp301_g19_recheck.log"

QUERIES = (b"VE?", b"TE?", b"TB?",
           b"1ID?", b"2ID?", b"3ID?",
           b"1TP", b"2TP", b"3TP",
           b"1MD?", b"2MD?", b"3MD?",
           b"1MO?", b"2MO?", b"3MO?")


def main() -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = LOG_PATH.open("a", encoding="utf-8")

    def say(msg: object = "") -> None:
        print(msg)
        out.write(f"{msg}\n")
        out.flush()

    def collect(s: serial.Serial, seconds: float) -> bytes:
        """Read whatever arrives within `seconds`. Transmits nothing."""
        end = time.time() + seconds
        buf = bytearray()
        while time.time() < end:
            n = s.in_waiting
            if n:
                buf += s.read(n)
                end = time.time() + 0.3   # extend a little after each burst
            else:
                time.sleep(0.02)
        return bytes(buf)

    say("\n" + "=" * 72)
    say(f"ESP301 link check  {datetime.datetime.now():%Y-%m-%d %H:%M:%S %z}  (read-only)")
    say("=" * 72)

    any_reply = False
    for baud in BAUDS:
        say(f"\n--- {PORT} @ {baud} ---")
        try:
            s = serial.Serial()
            s.port, s.baudrate, s.timeout, s.write_timeout = PORT, baud, 1.0, 2.0
            s.open()
        except Exception as exc:
            say(f"  OPEN FAILED: {exc!r}")
            say("  -> something else holds the port; this is not a controller fault.")
            continue

        try:
            say(f"  modem lines: cts={s.cts} dsr={s.dsr} ri={s.ri} cd={s.cd}")
            say(f"  in_waiting at open: {s.in_waiting}")

            say("  passive listen 5 s (transmitting nothing)...")
            passive = collect(s, 5.0)
            say(f"    -> {passive!r}  ({len(passive)} bytes)")
            any_reply |= bool(passive)

            for cmd in QUERIES:
                s.reset_input_buffer()
                s.write(cmd + b"\r")
                r = collect(s, 1.2)
                any_reply |= bool(r)
                say(f"    {cmd.decode():6} -> {r!r}")
        finally:
            s.close()

    say("\nVERDICT: " + (
        "the controller IS replying — inspect TE?/TB? above for a latched error."
        if any_reply else
        "ZERO bytes at every baud, and silent when passively listened to. The "
        "controller's CPU is not servicing its serial interface (state G19). It "
        "will not self-recover. Read the FRONT-PANEL ERROR before power-cycling."
    ))
    say(f"(transcript appended to {LOG_PATH})")
    out.close()
    return 0 if any_reply else 1


if __name__ == "__main__":
    raise SystemExit(main())
