"""How XPSController spins a group that has no spin commands.

Our GROUP1 is declared SingleAxisInUse, so ``GroupSpinParametersGet`` answers -18,
"wrong object type for this command". It does not need those commands: the RGV100BL is
configured with travel limits of +-165,000,000 degrees -- Newport's way of saying "this
axis rotates continuously" while keeping the SingleAxis command set -- so an ordinary
move toward that limit IS a spin, for 63 hours at the 2 rev/s ceiling.

What is checked here is the part that cannot be seen on the bench:

  * the strategy is chosen from the group's declared category, not hardcoded, so a
    controller reconfigured to SpindleAxis silently starts using the native commands;
  * the long move goes out on a SECOND connection. GroupMoveAbsolute blocks its socket
    until the move completes, which here is never, so issuing it on the primary socket
    would deadlock every subsequent read -- including the abort meant to end it;
  * the abort therefore goes out on the PRIMARY socket;
  * the target stops short of the limit rather than landing on it.

A fake XPS driver stands in for the hardware; no controller is contacted.

    python test/xps_spin_strategy_test.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from control_readout.newport_xps.controller import (  # noqa: E402
    _LIMIT_MARGIN,
    XPSController,
)

_fails: list[str] = []

ADDRESS = ("GROUP1", "GROUP1.POSITIONER")
LOW, HIGH = -165_000_000.0, 165_000_000.0


def check(ok: bool, msg: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        _fails.append(msg)


class _FakeDriver:
    """One XPS socket. Records every command, in order, with the sid it arrived on."""

    def __init__(self, log: list, sid: int, block_moves: bool = True,
                 spindle: bool = False) -> None:
        self._log = log
        self._sid = sid
        self._block_moves = block_moves
        self._spindle = spindle
        self.position = 25.0
        self._moving = False

    # -- the calls the controller makes --
    def GroupSpinParametersSet(self, sid, group, velocity, accel):
        self._log.append(("spin_native", sid, group, velocity))
        return 0, ""

    def GroupSpinParametersGet(self, sid, group):
        self._log.append(("spin_params_get", sid, group))
        if not self._spindle:
            # What a SingleAxis group really answers: -18, wrong object type.
            return -18, 0.0, 0.0
        return 0, 0.0, 720.0

    def GroupSpinModeStop(self, sid, group, accel):
        self._log.append(("spin_native_stop", sid, group))
        return 0, ""

    def PositionerSGammaParametersSet(self, sid, positioner, velo, accel, jmin, jmax):
        self._log.append(("set_velocity", sid, positioner, velo))
        return 0, ""

    def PositionerUserTravelLimitsGet(self, sid, positioner):
        return 0, LOW, HIGH

    def GroupMoveAbsolute(self, sid, group, targets):
        self._log.append(("move", sid, group, targets[0]))
        self._moving = True
        # The real call does not return until the move ends. That is the whole reason a
        # second socket exists, so the fake has to reproduce it.
        while self._block_moves and self._moving:
            time.sleep(0.005)
        return -27, ""       # what an aborted move actually answers

    def GroupMoveAbort(self, sid, group):
        self._log.append(("abort", sid, group))
        if not self._moving:
            return -22, ""   # nothing in progress
        self._moving = False
        return 0, ""

    def GroupVelocityCurrentGet(self, sid, group, n):
        return 0, 180.0

    def GroupPositionCurrentGet(self, sid, group, n):
        return 0, self.position

    def Login(self, sid, user, pw):
        self._log.append(("login", sid))
        return 0, ""

    def TCP_ConnectToServer(self, host, port, timeout):
        return self._sid

    def TCP_CloseSocket(self, sid):
        self._log.append(("close", sid))


class _FakeXPS:
    """Stands in for the NewportXPS client wrapper."""

    def __init__(self, log: list, category: str) -> None:
        self._xps = _FakeDriver(log, sid=0, spindle=category.lower() == "spindleaxis")
        self._sid = 0
        self.groups = {"GROUP1": {"category": category, "positioners": ["GROUP1.POSITIONER"]}}
        self.stages = {"GROUP1.POSITIONER": {}}
        self._log = log

    def check_error(self, err, msg=""):
        if err != 0:
            raise AssertionError(f"unexpected error {err}: {msg}")

    def get_stage_position(self, positioner):
        return self._xps.position


def _controller(category: str = "singleaxisinuse"):
    """A connected controller whose sockets are fakes. Never touches the network."""
    log: list = []
    c = XPSController("fake-host", username="u", password="p")
    c._xps = _FakeXPS(log, category)
    # The second connection the spin path opens: same fake driver class, sid 1, and
    # non-blocking so a test that never stops the spin still finishes.
    second = _FakeDriver(log, sid=1)
    c._spin_socket = lambda: (second, 1)   # type: ignore[method-assign]
    return c, log, second


# -- strategy selection -----------------------------------------------------------------
def test_a_spindle_group_uses_the_native_commands() -> None:
    c, log, _second = _controller(category="SpindleAxis")
    c.spin(ADDRESS, 180.0)
    kinds = [e[0] for e in log]
    check("spin_native" in kinds, f"a SpindleAxis group spins natively (got {kinds})")
    check("move" not in kinds, "and issues no long move")


def test_a_single_axis_group_uses_a_long_move() -> None:
    c, log, second = _controller()
    try:
        c.spin(ADDRESS, 180.0)
        time.sleep(0.05)
        kinds = [e[0] for e in log]
        check("move" in kinds, f"a SingleAxis group spins by moving (got {kinds})")
        check("spin_native" not in kinds,
              "and never sends a spin command the group would reject")
    finally:
        second._moving = False


def test_the_native_path_is_not_probed_on_a_single_axis_group() -> None:
    """GroupSpinParametersGet answers -18 there; asking would raise, not degrade."""
    c, log, second = _controller()
    try:
        c.spin(ADDRESS, 180.0)
        time.sleep(0.05)
        check(not any(e[0] == "spin_params_get" for e in log),
              f"no spin-parameter read on the SingleAxis path (got {[e[0] for e in log]})")
    finally:
        second._moving = False


# -- the two sockets --------------------------------------------------------------------
def test_the_long_move_goes_out_on_the_second_socket() -> None:
    """The point of the whole design: a blocking call must not occupy the main socket."""
    c, log, second = _controller()
    try:
        c.spin(ADDRESS, 180.0)
        time.sleep(0.05)
        moves = [e for e in log if e[0] == "move"]
        check(len(moves) == 1, f"exactly one move issued (got {moves})")
        check(moves[0][1] == 1, f"on socket 1, not the primary socket 0 (got sid {moves[0][1]})")
    finally:
        second._moving = False


def test_the_primary_socket_stays_usable_while_spinning() -> None:
    c, _log, second = _controller()
    try:
        c.spin(ADDRESS, 180.0)
        time.sleep(0.05)
        # Would block forever if the move had gone out on socket 0.
        check(c.get_position(ADDRESS) == 25.0,
              "position is still readable with the plate turning")
        check(c.spin_current(ADDRESS)[0] == 180.0, "and so is the velocity")
    finally:
        second._moving = False


def test_the_abort_goes_out_on_the_primary_socket() -> None:
    """The spin socket is inside the blocking move and cannot carry the command ending it."""
    c, log, _second = _controller()
    c.spin(ADDRESS, 180.0)
    time.sleep(0.05)
    log.clear()
    c.stop_spin(ADDRESS)
    aborts = [e for e in log if e[0] == "abort"]
    check(aborts and aborts[0][1] == 0,
          f"abort issued on socket 0 (got {aborts})")


def test_stopping_joins_the_move_thread() -> None:
    c, _log, _second = _controller()
    c.spin(ADDRESS, 180.0)
    time.sleep(0.05)
    check(c._spin_thread is not None and c._spin_thread.is_alive(), "a move thread is running")
    c.stop_spin(ADDRESS)
    check(c._spin_thread is None, "and stopping clears it rather than leaking it")


def test_stopping_a_stopped_plate_is_not_an_error() -> None:
    """Every lifecycle path calls this unconditionally; -22 means 'nothing was moving'."""
    c, _log, _second = _controller()
    try:
        c.stop_spin(ADDRESS)
        check(True, "stopping when not spinning is a no-op, not a raise")
    except Exception as exc:
        check(False, f"stopping when not spinning raised {exc!r}")


# -- the target -------------------------------------------------------------------------
def test_the_target_stops_short_of_the_limit() -> None:
    c, log, second = _controller()
    try:
        c.spin(ADDRESS, 180.0)
        time.sleep(0.05)
        target = [e for e in log if e[0] == "move"][0][3]
        check(target == HIGH - _LIMIT_MARGIN,
              f"a positive spin aims just inside the high limit (got {target})")
    finally:
        second._moving = False


def test_direction_follows_the_sign() -> None:
    c, log, second = _controller()
    try:
        c.spin(ADDRESS, -180.0)
        time.sleep(0.05)
        target = [e for e in log if e[0] == "move"][0][3]
        check(target == LOW + _LIMIT_MARGIN,
              f"a negative spin aims at the low limit (got {target})")
        velocity = [e for e in log if e[0] == "set_velocity"][0][3]
        check(velocity == 180.0,
              f"with the velocity set as a magnitude, the sign living in the target "
              f"(got {velocity})")
    finally:
        second._moving = False


def test_re_rating_replaces_the_move() -> None:
    """A SingleAxis re-rate cannot be seamless: the move in flight has to end first."""
    c, log, second = _controller()
    try:
        c.spin(ADDRESS, 180.0)
        time.sleep(0.05)
        log.clear()
        c.spin(ADDRESS, 360.0)
        time.sleep(0.05)
        kinds = [e[0] for e in log]
        check(kinds.index("abort") < kinds.index("move"),
              f"the old move is aborted before the new one goes out (got {kinds})")
        check([e for e in log if e[0] == "set_velocity"][0][3] == 360.0,
              "at the new rate")
    finally:
        second._moving = False


def test_a_zero_velocity_is_refused() -> None:
    c, _log, _second = _controller()
    try:
        c.spin(ADDRESS, 0.0)
        check(False, "a zero-velocity spin should not be accepted")
    except Exception:
        check(True, "a zero-velocity spin is refused rather than parking a dead move")


# -- headroom ---------------------------------------------------------------------------
def test_headroom_is_reported_in_seconds() -> None:
    c, _log, _second = _controller()
    hours = c.spin_headroom(ADDRESS, 180.0) / 3600.0
    check(abs(hours - (HIGH - 25.0) / 180.0 / 3600.0) < 1e-6,
          f"headroom is distance-to-limit over rate (got {hours:.1f} h)")
    check(hours > 250.0, f"which at 0.5 rev/s is over ten days ({hours:.0f} h)")


def test_a_spindle_group_has_no_headroom_to_run_out_of() -> None:
    c, _log, _second = _controller(category="SpindleAxis")
    check(c.spin_headroom(ADDRESS, 180.0) == float("inf"),
          "a SpindleAxis group reports unbounded headroom")


TESTS = [
    test_a_spindle_group_uses_the_native_commands,
    test_a_single_axis_group_uses_a_long_move,
    test_the_native_path_is_not_probed_on_a_single_axis_group,
    test_the_long_move_goes_out_on_the_second_socket,
    test_the_primary_socket_stays_usable_while_spinning,
    test_the_abort_goes_out_on_the_primary_socket,
    test_stopping_joins_the_move_thread,
    test_stopping_a_stopped_plate_is_not_an_error,
    test_the_target_stops_short_of_the_limit,
    test_direction_follows_the_sign,
    test_re_rating_replaces_the_move,
    test_a_zero_velocity_is_refused,
    test_headroom_is_reported_in_seconds,
    test_a_spindle_group_has_no_headroom_to_run_out_of,
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
    print("all XPS spin-strategy checks passed")
