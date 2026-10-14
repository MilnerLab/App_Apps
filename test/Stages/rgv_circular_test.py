"""The RGV is a circle, not a line.

After a spin the XPS's own coordinate is a running total -- 1,893 deg after a few
revolutions, millions after an hour of free-running. Handing an orientation straight to
``move_absolute`` therefore commands an UNWIND: ask for 93 deg while the controller reads
1,893 and the plate rewinds five full turns to reach an orientation it is already at.

So the RGV device reports position mod 360 and reaches any target by the shortest
rotation. What is pinned here is that the two halves agree -- that a reported orientation,
handed straight back as a target, is a no-op rather than a rewind.

    python test/rgv_circular_test.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from control_readout.newport_xps.rgv100bl.rgv100bl_device import RGV  # noqa: E402

_fails: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        _fails.append(msg)


class _FakeController:
    """A rotator with an unbounded coordinate, like the real one."""

    def __init__(self, raw: float = 0.0) -> None:
        import threading
        self._lock = threading.RLock()
        self.raw = raw
        self.moves: list[float] = []
        self.absolutes: list[float] = []

    def get_position(self, address):
        return self.raw

    def move_relative(self, address, delta):
        self.moves.append(delta)
        self.raw += delta

    def move_absolute(self, address, value):
        self.absolutes.append(value)
        self.raw = value


def _rgv(raw: float) -> tuple[RGV, _FakeController]:
    c = _FakeController(raw)
    return RGV("rot", group="GROUP1", controller=c, positioner="POSITIONER"), c


# -- reporting ---------------------------------------------------------------------------
def test_position_is_reported_as_an_orientation() -> None:
    r, c = _rgv(1893.444)
    check(abs(r.position() - 93.444) < 1e-6,
          f"a raw 1,893.444 deg reads as 93.444 (got {r.position()})")
    check(abs(r.raw_position() - 1893.444) < 1e-6,
          "and the raw controller coordinate is still available underneath")


def test_a_negative_coordinate_still_reads_forward() -> None:
    r, _c = _rgv(-30.0)
    check(abs(r.position() - 330.0) < 1e-6,
          f"-30 deg reads as 330, not as a negative orientation (got {r.position()})")


# -- the move that used to unwind ---------------------------------------------------------
def test_moving_to_the_current_orientation_does_nothing() -> None:
    """The reported bug, at its sharpest: 1,893 -> 'go to 93' must not rewind five turns."""
    r, c = _rgv(1893.0)
    r.move_to(93.0)
    check(not c.absolutes, "no absolute move is issued")
    check(abs(c.moves[0]) < 1e-9,
          f"and the relative move is zero, not -1,800 deg (got {c.moves[0]})")


def test_the_shortest_way_round_is_taken() -> None:
    r, c = _rgv(1893.0)          # orientation 93
    r.move_to(83.0)
    check(abs(c.moves[0] + 10.0) < 1e-6, f"93 -> 83 is -10 deg (got {c.moves[0]})")

    r, c = _rgv(350.0)
    r.move_to(10.0)
    check(abs(c.moves[0] - 20.0) < 1e-6,
          f"350 -> 10 crosses zero forwards by 20 deg, not back by 340 (got {c.moves[0]})")

    r, c = _rgv(10.0)
    r.move_to(350.0)
    check(abs(c.moves[0] + 20.0) < 1e-6,
          f"and the reverse is -20 deg (got {c.moves[0]})")


def test_no_move_ever_exceeds_half_a_turn() -> None:
    for raw in (0.0, 93.0, 359.9, -400.0, 1_000_000.0):
        for target in (0.0, 45.0, 179.0, 181.0, 359.0, -90.0, 725.0):
            r, c = _rgv(raw)
            r.move_to(target)
            if abs(c.moves[0]) > 180.0 + 1e-9:
                check(False, f"raw {raw} -> {target} moved {c.moves[0]} deg")
                return
    check(True, "no target on any coordinate costs more than 180 deg of rotation")


def test_the_move_lands_on_the_requested_orientation() -> None:
    r, c = _rgv(1893.444)
    r.move_to(200.0)
    check(abs(r.position() - 200.0) < 1e-6,
          f"the plate ends up at the orientation asked for (got {r.position()})")


def test_a_reported_angle_fed_back_is_a_no_op() -> None:
    """The round trip the panel performs every time it reads then writes the field."""
    r, c = _rgv(123_456.789)
    r.move_to(r.angle().Deg)
    check(abs(c.moves[0]) < 1e-6,
          f"reading the angle and commanding it back moves nothing (got {c.moves[0]})")


TESTS = [
    test_position_is_reported_as_an_orientation,
    test_a_negative_coordinate_still_reads_forward,
    test_moving_to_the_current_orientation_does_nothing,
    test_the_shortest_way_round_is_taken,
    test_no_move_ever_exceeds_half_a_turn,
    test_the_move_lands_on_the_requested_orientation,
    test_a_reported_angle_fed_back_is_a_no_op,
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
    print("all RGV circular-position checks passed")
