"""Go/no-go: does THIS transport honor the ESP301's CTS/RTS hardware flow control?

Newport documents a CTS/RTS handshake to stop the ESP301's 512-byte input command buffer
overflowing (User's Manual §3.2.1) — and the wedge trigger ("host writes without draining",
FM-2 / H3) IS that overflow. Whether `rtscts=True` actually helps depends on the transport,
and this script is the decision procedure. Defaults to COM2 @ 19200 (RS-232); override with
`--port`/`--baud`. Run against a healthy, FREE link (confirm with `esp301_link_check.py` first).

    App_Apps\\.venv\\Scripts\\python.exe App_Apps-xcorr\\tools\\verify_esp301_flowcontrol.py
    ...  --stress 120                       # ADD the decisive overflow test
    ...  --stress 60 --port COM7 --baud 921600   # the (historical) USB test

RESULTS ON RECORD (2026-07-22):
  * RS-232 (COM2 @ 19200): PASS. rtscts=True throttled the host (held_off_on_cts=True), only
    ~1,196 undrained writes in 120 s, link SURVIVED. rtscts is ADOPTED for RS-232.
  * USB / TI-3410 (COM7 @ 921600): FAIL. 817,818 writes, no holdoff, link WEDGED. The VCP
    never relays CTS. Do NOT enable rtscts over USB.

What it does
------------
SAFE checks (always): opens the port (a) WITHOUT and (b) WITH rtscts and confirms normal
request-response works either way (writes don't stall, `VE?`/`TP` reply); reports the CTS line.

DECISIVE test (`--stress SECONDS`, opt-in): reproduces the H3 overflow — a write-flood with
NO draining — WITH `rtscts=True`. Two outcomes:
  * host held off on CTS and/or the link still answers `1TP` afterwards -> flow control IS
    honored; the wedge is prevented; ADOPT rtscts on this transport.
  * the link goes silent (zero bytes) -> flow control is NOT honored; do NOT adopt rtscts.
    Use a duration >= 60 s for a conclusive negative. Recovery differs by transport: RS-232
    buffer overflow is *recoverable* (drains itself); a USB wedge needs re-enumeration.
    **Run --stress supervised**, able to recover the link if it wedges.

This script sends only `VE?`, `<n>TP`, and (in --stress) `TE?` — all queries, NO motion.
"""
from __future__ import annotations

import argparse
import sys
import time

from control_readout.esp_301.controller import ESP301Controller, ESP301Error

PORT = "COM2"      # RS-232 (2026-07-22); was COM7 over the USB bridge. Override with --port.
USB_BAUD = 19200   # RS-232 front-panel baud; was 921600 over USB. Override with --baud.


def _modem(s) -> str:
    return f"cts={s.cts} dsr={s.dsr} cd={s.cd} ri={s.ri}"


def _safe_checks(say) -> bool:
    say("[1] baseline open (rtscts=False) — must be healthy to begin ...")
    esp = ESP301Controller(port=PORT, baud=USB_BAUD)  # no flow control
    esp.connect()
    try:
        say(f"    modem: {_modem(esp.serial)}")
        ve = esp._query("VE?")
        say(f"    VE? -> {ve!r}")
        for ax in (1, 2, 3):
            say(f"    {ax}TP -> {esp._query(f'{ax}TP')!r}")
        healthy = bool(ve)
    finally:
        esp.disconnect()
    if not healthy:
        say("    NOT HEALTHY (no VE? reply). Recover the link first (esp301_link_check.py). Aborting.")
        return False

    say("\n[2] open with rtscts=True, write_timeout=2.0 — normal comms must be unaffected ...")
    esp2 = ESP301Controller(port=PORT, baud=USB_BAUD, rtscts=True, write_timeout=2.0)
    esp2.connect()
    try:
        say(f"    modem: {_modem(esp2.serial)}")
        try:
            ve = esp2._query("VE?")
            say(f"    VE? -> {ve!r}")
            for ax in (1, 2, 3):
                say(f"    {ax}TP -> {esp2._query(f'{ax}TP')!r}")
            ok = bool(ve)
        except Exception as exc:
            say(f"    WRITE/READ FAILED with rtscts on: {exc!r}")
            say("    -> the bridge is NOT driving CTS during normal traffic (writes stalled).")
            say("    -> do NOT adopt rtscts as-is; the controller-side handshake may be off, or")
            say("       the TI-3410 does not relay CTS. See the handoff.")
            ok = False
    finally:
        esp2.disconnect()

    say("")
    if ok:
        say("SAFE CHECKS PASS: rtscts=True does not break normal request-response.")
        say("Whether it actually PREVENTS the overflow wedge is proven only by --stress.")
    else:
        say("SAFE CHECKS FAILED with rtscts on (see above): flow control is not usable as-is.")
    return ok


def _link_alive(say) -> bool:
    """Raw baseline probe: does 1TP come back? (True = link alive.)"""
    import serial as _serial
    s = _serial.Serial(PORT, USB_BAUD, timeout=1.0)
    try:
        s.reset_input_buffer()
        s.write(b"1TP\r")
        time.sleep(0.15)
        r = s.read(s.in_waiting or 1)
        say(f"    1TP -> {r!r}")
        return bool(r)
    finally:
        s.close()


def _stress(seconds: float, write_timeout: float, say) -> bool:
    say(f"\n[3] STRESS: write-flood (NO draining) for {seconds:.0f}s WITH rtscts=True ...")
    say("    reproduces the H3 overflow; if CTS is honored the host is throttled and the")
    say("    link survives. If not, it wedges (RS-232 self-drains; USB needs re-enumeration).")
    import serial as _serial
    s = _serial.Serial(PORT, USB_BAUD, timeout=1.0, rtscts=True, write_timeout=write_timeout)
    n = 0
    held_off = False
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                s.write(b"TE?\r")  # benign query, NO read -> undrained-write flood
                n += 1
            except _serial.SerialTimeoutException:
                # a write blocked past write_timeout = CTS held us off = flow control ACTIVE
                held_off = True
                break
    finally:
        s.close()
    say(f"    wrote {n} undrained commands; write_held_off_on_cts={held_off}")

    time.sleep(0.5)
    say("[4] post-stress link check ...")
    alive = _link_alive(say)
    say("")
    if alive:
        say("VERDICT: link SURVIVED the overflow flood with rtscts=True.")
        if held_off:
            say("  + writes were held off on CTS -> flow control is actively throttling.")
        say(f"  => This transport ({PORT} @ {USB_BAUD}) honors CTS. ADOPT rtscts=True")
        say("     (+ write_timeout) for it, backed by the drain scaffolding. Re-run the")
        say("     resync mock test + a supervised e2e after adopting.")
    else:
        say("VERDICT: link WEDGED despite rtscts=True (zero bytes on 1TP).")
        say(f"  => This transport ({PORT} @ {USB_BAUD}) does NOT relay CTS. Do NOT adopt")
        say("     rtscts here; keep the drain scaffolding (the software prevention).")
        say("  => Recover: on RS-232 the overflow drains itself (retry after a moment); on")
        say("     USB re-enumerate USB\\VID_104D&PID_3001 (replug / Disable-Enable-PnpDevice),")
        say("     then esp301_link_check.py.")
    return alive


def main(argv: list[str] | None = None) -> int:
    global PORT, USB_BAUD
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stress", type=float, default=0.0, metavar="SECONDS",
                   help="run the decisive overflow flood for SECONDS (>=60 for a conclusive "
                        "negative). MAY WEDGE the link; only run supervised.")
    p.add_argument("--write-timeout", type=float, default=2.0,
                   help="write_timeout for the rtscts opens, s (default 2.0)")
    p.add_argument("--port", default=PORT, help=f"serial port (default {PORT})")
    p.add_argument("--baud", type=int, default=USB_BAUD, help=f"baud (default {USB_BAUD})")
    args = p.parse_args(argv)

    PORT, USB_BAUD = args.port, args.baud

    def say(msg: object = "") -> None:
        print(msg)

    say("ESP301 flow-control verification (rtscts / CTS-RTS handshake)")
    say("=" * 64)
    try:
        ok = _safe_checks(say)
        if not ok:
            return 1
        if args.stress > 0:
            survived = _stress(args.stress, args.write_timeout, say)
            return 0 if survived else 2
        say("\n(Skipped the decisive overflow test. Re-run with --stress 60 — supervised, "
            "when you can re-enumerate — to prove rtscts prevents the wedge.)")
        return 0
    except ESP301Error as exc:
        say(f"\nESP301 error: {exc}")
        return 1
    except KeyboardInterrupt:
        say("\ninterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
