"""The reusable scan subroutines: patterns, TranslationStage, TranslationStageScan.

What is worth pinning here is the *contract between a routine and its scan*, because
that is what a second scan routine will be written against:

* Coming back round the loop is how the caller says "done with that position". There is
  no separate call to forget, and falling out of the loop means every wanted position was
  visited -- not that something went wrong quietly.
* ``before_move`` runs BEFORE the move, and whatever it raises leaves the iteration. That
  is the only hook a routine needs in order to own pause and abort; the scan itself holds
  no run-control state at all.
* ``on_settled`` runs AFTER the move returned, so anything it enables is enabled only
  while the stage is genuinely stationary.
* Limits come from the stage's own StageSpec, so an illegal position is refused by the
  hardware's own numbers rather than by a table kept alongside the scan.

No pytest, no hardware, no Qt:

    PYTHONPATH=. .venv/bin/python test/test_translation_stage_scan.py
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base_core.framework.routines.run_control import RunControl, ScanAborted  # noqa: E402
from base_core.ipc.blocking import WorkerCallError  # noqa: E402
from control_readout.base.stage_spec import StageLimitError  # noqa: E402
from control_readout.esp_301.fms300pp import spec as fms300pp_spec  # noqa: E402

from app_apps.routines.scanning.patterns import (  # noqa: E402
    ExplicitPattern,
    ScanPatternError,
    StepRulePattern,
    UniformPattern,
)
from app_apps.routines.scanning.translation_stage import TranslationStage  # noqa: E402
from app_apps.routines.scanning.translation_stage_scan import (  # noqa: E402
    TranslationStageScan,
)

_fails: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        _fails.append(msg)


def raises(exc, fn, msg: str) -> None:
    try:
        fn()
    except exc:
        check(True, msg)
    except Exception as other:  # noqa: BLE001
        check(False, f"{msg} (raised {type(other).__name__}: {other})")
    else:
        check(False, f"{msg} (nothing raised)")


# --- fakes ----------------------------------------------------------------


class FakeHandle:
    """The two members of a stage handle that TranslationStage touches."""

    SPEC = fms300pp_spec.SPEC

    def __init__(self, fail_with: str | None = None, never_reply: bool = False) -> None:
        self.commanded: list[float] = []
        self._fail_with = fail_with
        self._never_reply = never_reply

    def move_to(self, position, on_done, on_error) -> None:
        self.commanded.append(position)
        if self._never_reply:
            return
        if self._fail_with is not None:
            on_error(self._fail_with)
            return
        on_done()


def _stage(handle=None, **kw) -> TranslationStage:
    return TranslationStage(handle or FakeHandle(), role="probe", timeout_s=1.0, **kw)


# --- patterns -------------------------------------------------------------


def test_uniform_pattern_is_expand_range():
    p = UniformPattern(start=0.0, stop=10.0, step=2.5, name="probe")
    check(p.positions() == (0.0, 2.5, 5.0, 7.5, 10.0), f"evenly spaced, inclusive: {p.positions()}")
    check("uniform" in p.describe() and "2.5" in p.describe(),
          f"describes itself for provenance: {p.describe()!r}")


def test_uniform_pattern_can_force_the_endpoint():
    """So two runs with different steps still share one right edge for analysis."""
    p = UniformPattern(0.0, 10.0, 3.0, name="probe", include_endpoint=True)
    check(p.positions() == (0.0, 3.0, 6.0, 9.0, 10.0), f"short final step appended: {p.positions()}")


def test_explicit_pattern_takes_positions_verbatim():
    p = ExplicitPattern([3.0, 1.0, 2.0])
    check(p.positions() == (3.0, 1.0, 2.0), "order is the caller's, not sorted")
    raises(ScanPatternError, lambda: ExplicitPattern([]).positions(),
           "an empty explicit pattern is refused, not silently a zero-point scan")


def test_step_rule_pattern_asks_its_rule_once():
    calls = []

    def rule() -> float:
        calls.append(1)
        return 2.0

    p = StepRulePattern(0.0, 4.0, rule, name="probe")
    check(p.positions() == (0.0, 2.0, 4.0), f"spacing came from the rule: {p.positions()}")
    p.describe()
    p.positions()
    check(len(calls) == 1,
          f"and the rule is asked exactly once, so the positions and the recorded "
          f"description can never disagree ({len(calls)} calls)")


def test_a_non_positive_step_is_refused_before_anything_moves():
    raises(ScanPatternError, lambda: UniformPattern(0.0, 10.0, 0.0, name="probe").positions(),
           "a zero step is a plan error, not an infinite scan")


# --- TranslationStage -----------------------------------------------------


def test_limits_come_from_the_stage_not_from_the_scan():
    s = _stage()
    check(s.limits == fms300pp_spec.SPEC.limits and s.units == "mm",
          f"reads its own spec off the handle: {s.limits} {s.units}")
    raises(StageLimitError, lambda: s.move_to(9999.0),
           "a position past the soft limit is refused by the stage's own numbers")


def test_the_move_is_commanded_and_blocks_on_the_reply():
    h = FakeHandle()
    _stage(h).move_to(150.0)
    check(h.commanded == [150.0], f"exactly one absolute move was issued: {h.commanded}")


def test_a_device_error_becomes_an_exception_not_a_silent_success():
    """Every other handle in the repo discards the error. A routine must not."""
    s = _stage(FakeHandle(fail_with="axis 1 is not responding"))
    raises(WorkerCallError, lambda: s.move_to(150.0),
           "an on_error reply raises, carrying the device's own message")


def test_a_move_that_is_never_answered_times_out():
    s = _stage(FakeHandle(never_reply=True))
    raises(WorkerCallError, lambda: s.move_to(150.0),
           "a lost reply times out rather than hanging the run forever")


def test_before_move_fires_on_every_move_including_a_rejected_one():
    hits = []
    s = _stage(before_move=lambda: hits.append("x"))
    s.move_to(150.0)
    try:
        s.move_to(9999.0)
    except StageLimitError:
        pass
    check(len(hits) == 2, f"fires before the limit check, not after it ({len(hits)})")


# --- TranslationStageScan -------------------------------------------------


def test_each_iteration_moves_once_and_yields_that_position():
    h = FakeHandle()
    scan = TranslationStageScan(_stage(h), [70.0, 71.0, 72.0], offset=80.0)
    seen = [(pt.index, pt.base, pt.commanded) for pt in scan]
    check(h.commanded == [150.0, 151.0, 152.0], f"commanded = base + offset: {h.commanded}")
    check(seen == [(0, 70.0, 150.0), (1, 71.0, 151.0), (2, 72.0, 152.0)],
          f"and each point carries both frames: {seen}")


def test_falling_out_of_the_loop_means_every_position_was_visited():
    scan = TranslationStageScan(_stage(), [1.0, 2.0])
    n = sum(1 for _ in scan)
    check(n == scan.n == 2, f"the loop ran exactly n times ({n})")
    check(scan.positions == (1.0, 2.0), "and the scan still reports what it was asked to do")


def test_an_empty_scan_yields_nothing_rather_than_moving():
    h = FakeHandle()
    check(list(TranslationStageScan(_stage(h), [])) == [] and h.commanded == [],
          "no positions means no moves and no points")


def test_the_gate_hook_runs_before_the_move_and_the_settled_hook_after():
    order: list[str] = []
    h = FakeHandle()

    class Recording(FakeHandle):
        def move_to(self, position, on_done, on_error):
            order.append(f"move {position}")
            on_done()

    scan = TranslationStageScan(
        TranslationStage(Recording(), role="probe", timeout_s=1.0,
                         before_move=lambda: order.append("close")),
        [10.0],
        on_settled=lambda pt: order.append(f"open {pt.commanded}"),
    )
    list(scan)
    check(order == ["close", "move 10.0", "open 10.0"],
          f"shut, move, then open -- never open while moving: {order}")


def test_the_centre_is_the_middle_of_the_commanded_sweep():
    """Where the correlation peak sits, so where 'maximise the signal' means something."""
    scan = TranslationStageScan(_stage(), [0.0, 10.0, 20.0, 30.0, 40.0], offset=2.0)
    check(scan.centre == 22.0, f"middle base 20.0 + offset 2.0 (got {scan.centre})")


def test_the_scan_holds_no_run_control_of_its_own():
    """The routine owns pause and abort; the scan only calls the hook it was given."""
    control = RunControl()
    control.begin()
    h = FakeHandle()
    scan = TranslationStageScan(_stage(h), [1.0, 2.0, 3.0], before_move=control.checkpoint)

    collected = []
    try:
        for pt in scan:
            collected.append(pt.commanded)
            if pt.index == 1:
                control.abort()          # as an operator would, from another thread
    except ScanAborted:
        pass
    else:
        check(False, "an abort must surface as ScanAborted out of the iteration")
        return

    check(collected == [1.0, 2.0], f"the points already taken are kept: {collected}")
    check(h.commanded == [1.0, 2.0],
          f"and the position after the abort was never commanded: {h.commanded}")


def test_the_gate_is_checked_before_the_first_move_too():
    """An abort raised before the scan starts must not move the stage even once."""
    control = RunControl()
    control.begin()
    control.abort()
    h = FakeHandle()
    scan = TranslationStageScan(_stage(h), [1.0, 2.0], before_move=control.checkpoint)
    raises(ScanAborted, lambda: list(scan), "the very first next() raises")
    check(h.commanded == [], f"nothing was commanded: {h.commanded}")


def test_a_scan_can_be_walked_again_from_the_start():
    h = FakeHandle()
    scan = TranslationStageScan(_stage(h), [1.0, 2.0])
    list(scan)
    list(scan)
    check(h.commanded == [1.0, 2.0, 1.0, 2.0], f"__iter__ restarts the walk: {h.commanded}")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    for t in TESTS:
        print(f"\n--- {t.__name__}")
        try:
            t()
        except Exception:  # noqa: BLE001
            _fails.append(t.__name__)
            traceback.print_exc()
    print(f"\n{len(TESTS)} test(s), {len(_fails)} failure(s)")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
