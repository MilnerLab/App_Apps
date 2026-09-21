"""Step mode: the operator-advanced gate between grating setpoints.

A scan in step mode parks the probe at the centre of each setpoint's sweep, announces
the hold, and waits for the operator to press Step before sweeping. What is checked here
is the part that is easy to get wrong and impossible to notice on the bench: that the
gate actually blocks, that a press made *before* the gate is reached is not lost, that
an abort raised while parked is still noticed, and that the "holding" announcement is
always followed by a "released" one even when the wait ends badly -- a display left
believing it is still holding polls the scope forever.

No hardware, no Qt, no event loop:

    python test/xcorr_step_mode_test.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base_core.framework.events.event_bus import EventBus  # noqa: E402
from base_core.framework.routines.run_control import RunControl, ScanAborted  # noqa: E402

from app_apps.routines.xcorr.events import XcorrSteppingHold  # noqa: E402
from app_apps.routines.xcorr.routine import XcorrRoutine  # noqa: E402
from app_apps.routines.xcorr.storage import default_run_path  # noqa: E402

_fails: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        _fails.append(msg)


class _Setpoint:
    """The three fields the hold path reads off a planner Setpoint."""

    grating_mm = -12.5
    delay_mm = 18.0
    probe_offset_mm = 2.0
    #: Odd length on purpose: the parked position must be the MIDDLE of the sweep.
    probe_base_mm = [0.0, 10.0, 20.0, 30.0, 40.0]


class _StubStage:
    """The two members of ``TranslationStage`` the hold and sweep paths touch."""

    def __init__(self, role: str, moves: list) -> None:
        self.role = role
        self._moves = moves

    def move_to(self, position: float) -> None:
        self._moves.append((self.role, position))


def _routine():
    """A routine with its hardware and gate plumbing stubbed, nothing else patched."""
    bus = EventBus()
    r = XcorrRoutine.__new__(XcorrRoutine)
    r._bus = bus
    r._control = RunControl()
    r._recorder = None
    moves: list[tuple[str, float]] = []
    r._probe = _StubStage("probe", moves)
    gate: list[str] = []
    r._gate_close = lambda: gate.append("close")
    held: list[XcorrSteppingHold] = []
    bus.subscribe(XcorrSteppingHold, held.append)
    return r, moves, gate, held


def _hold(r, si: int, sp, n_setpoints: int) -> None:
    """Drive one alignment hold, building the probe sweep the way the run does."""
    r._hold_for_alignment(si, sp, r._probe_scan(si, sp), n_setpoints)


def _arm(r, presses: int = 0) -> None:
    """Put the routine in a running, stepping state with ``presses`` already banked."""
    r._control.begin()
    r.set_step_mode(True)
    if presses:
        r.step(presses)


def _park(r) -> threading.Event:
    """Wait at the step gate on a daemon thread; the Event is set once released."""
    done = threading.Event()

    def wait() -> None:
        try:
            r._control.wait_for_step(on_hold=r._gate_close)
        except ScanAborted:
            pass
        done.set()

    threading.Thread(target=wait, daemon=True).start()
    return done


# -- the gate ---------------------------------------------------------------------------
def test_off_by_default_costs_nothing() -> None:
    r, moves, gate, held = _routine()
    _hold(r, 0, _Setpoint(), 4)
    check(moves == [] and gate == [] and held == [],
          "with step mode off the hold is a no-op: no move, no gate, no event")


def test_the_probe_parks_at_the_sweep_centre() -> None:
    """Not the sweep's start, where the stage happens to be -- the peak is at the centre."""
    r, moves, _gate, _held = _routine()
    _arm(r, presses=1)
    _hold(r, 2, _Setpoint(), 4)
    check(moves == [("probe", 22.0)],
          f"probe parked at middle base 20.0 + offset 2.0 = 22.0 (got {moves})")


def test_the_hold_is_announced_then_released() -> None:
    r, _moves, _gate, held = _routine()
    _arm(r, presses=1)
    _hold(r, 1, _Setpoint(), 4)
    check([h.holding for h in held] == [True, False],
          f"one holding=True then one holding=False (got {[h.holding for h in held]})")
    first = held[0]
    check(first.setpoint_index == 1 and first.n_setpoints == 4,
          "the announcement carries which setpoint of how many")
    check(first.grating_mm == -12.5 and first.probe_mm == 22.0,
          "and the settled grating and parked probe positions")


def test_the_release_is_published_even_when_the_wait_aborts() -> None:
    """The finally clause. A display left holding polls the scope forever."""
    r, _moves, _gate, held = _routine()
    _arm(r)

    def boom(*_a, **_kw):
        raise RuntimeError("wait blew up")

    r._control.wait_for_step = boom
    try:
        _hold(r, 0, _Setpoint(), 2)
    except RuntimeError:
        pass
    check([h.holding for h in held] == [True, False],
          f"holding=False still went out (got {[h.holding for h in held]})")


def test_the_gate_actually_blocks_until_step() -> None:
    r, _moves, gate, _held = _routine()
    _arm(r)
    done = _park(r)

    check(not done.wait(0.35), "still parked after 350 ms with no press")
    check(gate == ["close"], "and the spectrum gate was shut while parked")
    r.step()
    check(done.wait(2.0), "released within a poll interval of the press")


def test_a_press_made_early_is_not_lost() -> None:
    """Permits accumulate: the count is what the operator pressed, not what was timed right."""
    r, _moves, _gate, _held = _routine()
    _arm(r, presses=3)
    for i in range(3):
        t0 = time.perf_counter()
        r._control.wait_for_step()
        check(time.perf_counter() - t0 < 0.05, f"setpoint {i + 1} passed immediately")
    done = _park(r)
    check(not done.wait(0.3), "and the fourth blocks -- exactly three were banked")


def test_abort_frees_a_parked_run() -> None:
    r, _moves, _gate, _held = _routine()
    _arm(r)
    done = _park(r)
    check(not done.wait(0.2), "parked")
    r.abort()
    check(done.wait(2.0), "an abort raised while parked is noticed, not deadlocked")


def test_disarming_frees_a_parked_run() -> None:
    """Turning step mode off must release a routine already waiting at the gate."""
    r, _moves, _gate, _held = _routine()
    _arm(r)
    done = _park(r)
    check(not done.wait(0.2), "parked")
    r.set_step_mode(False)
    check(done.wait(2.0), "runs on without waiting for a press it no longer needs")


def test_step_is_ignored_when_not_running() -> None:
    r, _moves, _gate, _held = _routine()
    r.set_step_mode(True)
    r.step(5)                       # not running yet -- banks nothing
    r._control.begin()
    done = _park(r)
    check(not done.wait(0.3),
          "presses before the run started banked nothing -- the gate still holds")


# -- run naming -------------------------------------------------------------------------
def test_run_name_is_sanitised_into_the_filename() -> None:
    from datetime import datetime
    when = datetime(2026, 9, 1, 13, 45, 30)
    out = Path("/tmp")
    check(default_run_path(out, when).name == "XCORR_20260901_134530.h5",
          "a blank name reproduces the original timestamp-only form")
    check(default_run_path(out, when, run_name="scan_L").name
          == "XCORR_scan_L_20260901_134530.h5", "a plain name is folded in")
    check(default_run_path(out, when, run_name="  scan/L:1  ").name
          == "XCORR_scan_L_1_20260901_134530.h5",
          "a slash and a colon are collapsed rather than reaching the filesystem")
    check(default_run_path(out, when, run_name="///").name
          == "XCORR_20260901_134530.h5",
          "a name that sanitises away falls back to the plain form, not to 'XCORR__'")
    check(default_run_path(out, when, run_name="a").name.endswith("_20260901_134530.h5"),
          "the timestamp always stays last so runs still sort chronologically")


TESTS = [
    test_off_by_default_costs_nothing,
    test_the_probe_parks_at_the_sweep_centre,
    test_the_hold_is_announced_then_released,
    test_the_release_is_published_even_when_the_wait_aborts,
    test_the_gate_actually_blocks_until_step,
    test_a_press_made_early_is_not_lost,
    test_abort_frees_a_parked_run,
    test_disarming_frees_a_parked_run,
    test_step_is_ignored_when_not_running,
    test_run_name_is_sanitised_into_the_filename,
]

if __name__ == "__main__":
    for t in TESTS:
        print(f"\n--- {t.__name__}")
        t()
    print()
    if _fails:
        print(f"FAILED ({len(_fails)}):")
        for f in _fails:
            print("  -", f)
        sys.exit(1)
    print("all step-mode checks passed")
