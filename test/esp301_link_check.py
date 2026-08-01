"""ESP301 link check — STRICTLY READ-ONLY. Originally written for defect G19 (USB wedge).

Run this when the ESP301 is not responding, or to confirm the link before a run, to find
out *what kind* of not-responding it is. It appends a timestamped transcript to
Docs/esp301_g19_recheck.log.

    App_Apps\\.venv\\Scripts\\python.exe App_Apps-xcorr\\test\\esp301_link_check.py

Current transport (2026-07-22): **RS-232, COM2 @ 19200 8N1.** Historically this was the
TI-3410 USB bridge (COM7 @ 921600); the USB-era hazard note below is kept as history.

Safety contract
---------------
This script cannot move a stage or change a setting. It sends only status queries — ``VE?``,
``TE?``, ``TB?``, ``<n>TP``, ``<n>MD?``, ``<n>MO?``, ``<n>ID?`` — at the configured baud. It
never sends ``PA``, ``PR``, ``ST``, ``MO``/``MF``, ``OR``, ``VA``, or a bare ``CR``. **Do NOT
run it during an experiment**: pyserial opens the port exclusively on Windows, so it either
fails port-busy or steals the port from the run. Only run it when the port is free.

Historical hazard (USB only): writing at a mismatched baud (19200 to a 921600 UART behind the
TI-3410 bridge) wedged the ESP301 parser until USB re-enumeration — the likely cause of the
older idle re-wedges in the recheck log. The tool therefore only WRITES at the matched rate
and, if silent, drops to a second baud only to PASSIVELY LISTEN. On a real RS-232 UART this
hazard does not apply (a wrong-baud write just yields recoverable framing errors), but the
discipline is harmless and retained.

Reading the result
------------------
* **Open fails** — something else holds the port. Not a controller fault; find the process.
* **Bytes come back** — the link is healthy. Check ``TE?``/``TB?`` for a latched error.
* **Zero bytes, passive listen silent** — the controller is not servicing its serial
  interface. Over USB this was the G19 state needing re-enumeration; over RS-232 check the
  cable, the front-panel baud, and the front-panel error display first.

Why raw reads instead of ``readline()``: ``readline()`` waits for a ``\\n`` and returns ``''``
both when nothing arrived and when bytes arrived without an LF terminator. Those are different
faults and must be told apart — the same ambiguity that made
``ESP301Controller.wait_for_motion`` misreport a dead link as a stuck axis (defect G20).
"""
from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path

import serial

PORT = "COM2"      # RS-232 (2026-07-22); was COM7 over the USB bridge.
#: The baud this tool WRITES at. RS-232 front-panel rate (was 921600 over USB).
USB_BAUD = 19200
#: A second baud tried only for PASSIVE listen if the primary is silent. Now equal to the
#: primary (both RS-232 19200); it differed in the USB era (921600 primary vs 19200), where
#: writing at this rate would have wedged the parser (G19) — hence listen-only here.
RS232_BAUD = 19200
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

    def probe(baud: int, *, write_queries: bool) -> bool:
        """Open the port at `baud`, passively listen, and — only if `write_queries` —
        send the status query set. Returns True if any byte came back.

        `write_queries` is the whole safety gate: we pass it True only at the matched/primary
        baud. Any secondary baud gets False (passive listen only) — a hold-over from the USB
        era where a mismatched-baud write was the documented G19 wedge trigger.
        """
        got = False
        try:
            s = serial.Serial()
            s.port, s.baudrate, s.timeout, s.write_timeout = PORT, baud, 1.0, 2.0
            s.open()
        except Exception as exc:
            say(f"  OPEN FAILED: {exc!r}")
            say("  -> something else holds the port; this is not a controller fault.")
            return False
        try:
            say(f"  modem lines: cts={s.cts} dsr={s.dsr} ri={s.ri} cd={s.cd}")
            say(f"  in_waiting at open: {s.in_waiting}")
            say("  passive listen 5 s (transmitting nothing)...")
            passive = collect(s, 5.0)
            say(f"    -> {passive!r}  ({len(passive)} bytes)")
            got |= bool(passive)
            if not write_queries:
                say("  (passive only — NOT writing at this baud; a mismatched-baud "
                    "write would wedge the parser, G19)")
                return got
            for cmd in QUERIES:
                s.reset_input_buffer()
                s.write(cmd + b"\r")
                r = collect(s, 1.2)
                got |= bool(r)
                say(f"    {cmd.decode():6} -> {r!r}")
        finally:
            s.close()
        return got

    # Primary baud first — the matched rate, where writing is safe.
    say(f"\n--- {PORT} @ {USB_BAUD} (matched rate — writes OK) ---")
    healthy = probe(USB_BAUD, write_queries=True)
    any_reply = healthy

    # Fall back to the secondary baud ONLY if the primary said nothing, and then PASSIVELY.
    # (In the USB era this guarded against a mismatched-baud write wedging the parser.)
    if not healthy:
        if RS232_BAUD != USB_BAUD:
            say(f"\n--- {PORT} @ {RS232_BAUD} (PASSIVE listen only — never write here) ---")
            any_reply |= probe(RS232_BAUD, write_queries=False)
    else:
        say(f"\n--- {PORT} @ {RS232_BAUD}: SKIPPED (primary baud is healthy) ---")

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
