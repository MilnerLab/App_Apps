"""The fast/slow correction toggle, and the capture that now arms itself.

Two behaviours the operator depends on and neither of which is visible from the plot:

  * Starting stabilization in slow mode arms the capture by itself. Before this, the
    tracker started OFF -- the cold per-frame loop -- and stayed there until someone
    pressed Capture reference, so the panel could say "slow" while the fast loop ran.
  * The toggle reaches TemplateState in both directions, and going fast means OFF
    rather than CAPTURING. Routing it through invalidate() would leave the loop
    capturing forever and correcting never, which looks identical to "it hung".

Also pinned here: an ordinary config edit must NOT disturb a locked template. That
costs a 5 s re-capture every time a spinbox is touched, and it is the failure mode a
naive "apply the mode on every config message" implementation walks straight into.

No hardware, no Qt:

    python test/correction_mode_test.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (  # noqa: E402
    FringeFitParams,
    StabilizationConfig,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_template import (  # noqa: E402
    PhaseTemplate,
)
from app_apps.analysis.phase_control.subprocess.domain.template_tracker import (  # noqa: E402
    TemplateState,
    TemplateTracker,
)
from app_apps.analysis.phase_control.subprocess.phase_stabilization_worker import (  # noqa: E402
    PhaseStabilizationWorker,
)

_fails: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        _fails.append(msg)


def _cfg() -> StabilizationConfig:
    """A default config. ``params`` has no default of its own, so it is supplied here."""
    return StabilizationConfig(params=FringeFitParams())


def _worker(slow: bool = True) -> PhaseStabilizationWorker:
    """A worker with only the fields the tracker paths touch. No IPC, no subprocess."""
    w = PhaseStabilizationWorker.__new__(PhaseStabilizationWorker)
    w._config = _cfg()
    w._config.slow_correction = slow
    w._tracker = None
    w._corrector = None
    w.notified: list = []
    w._notify = w.notified.append
    w._reply_ok = lambda msg: None
    w._last_state_stamp = None

    class _Averager:
        def __init__(self) -> None:
            self.resets = 0

        def reset(self) -> None:
            self.resets += 1

    w._averager = _Averager()
    return w


# -- the default ------------------------------------------------------------------------
def test_slow_is_the_default() -> None:
    check(_cfg().slow_correction is True,
          "a fresh config selects slow correction")


def test_the_default_survives_a_round_trip() -> None:
    cfg = _cfg()
    cfg.slow_correction = False
    restored = StabilizationConfig.from_primitive(cfg.to_primitive())
    check(restored.slow_correction is False, "the mode is saved and reloaded, not reset")
    # Backward compatibility: a config persisted before the toggle existed has no such
    # key, and must come back as slow rather than silently switching the operator to fast.
    older = cfg.to_primitive()
    older.pop("slow_correction")
    check(StabilizationConfig.from_primitive(older).slow_correction is True,
          "a config file written before this existed still reads as slow")


def _set_config(w: PhaseStabilizationWorker, msg_id: str) -> None:
    """Deliver the worker's current config back to it as a SetStabilizationConfig would.

    Through ``__wrapped__``: the handler is @worker_thread, and this suite runs no
    TaskRunner for it to be dispatched onto.
    """
    PhaseStabilizationWorker._on_set_config.__wrapped__(
        w, type("M", (), {"config": w._config, "id": msg_id})())


# -- auto-capture -----------------------------------------------------------------------
def test_starting_in_slow_mode_arms_the_capture() -> None:
    """The bug this fixes: the panel said slow while the cold loop ran."""
    w = _worker(slow=True)
    PhaseStabilizationWorker._build_tracker(w)
    check(w._tracker.state is TemplateState.CAPTURING,
          f"the tracker is capturing the moment stabilization starts (got {w._tracker.state})")


def test_starting_in_fast_mode_does_not() -> None:
    w = _worker(slow=False)
    PhaseStabilizationWorker._build_tracker(w)
    check(w._tracker.state is TemplateState.OFF,
          f"fast mode starts and stays on the cold loop (got {w._tracker.state})")


# -- the toggle -------------------------------------------------------------------------
def _template() -> PhaseTemplate:
    """A structurally valid template. The contents are irrelevant here -- these tests are
    about which STATE the tracker is in, not about what it fits."""
    return PhaseTemplate(x_ref=[800.0, 801.0, 802.0], f_ref=[1.0, 1.0, 1.0], amp_ref=1.0)


def _locked_tracker(cfg: StabilizationConfig) -> TemplateTracker:
    t = TemplateTracker(cfg)
    t.install(_template())
    return t


def test_switching_to_fast_goes_to_off_not_capturing() -> None:
    """OFF, not CAPTURING. The difference is a loop that corrects and one that holds."""
    cfg = _cfg()
    t = _locked_tracker(cfg)
    check(t.disable() is True, "disabling a locked tracker reports a real change")
    check(t.state is TemplateState.OFF, f"and lands in OFF (got {t.state})")
    check(t.template is None, "with the template dropped")
    check(t.disable() is False, "disabling again is a no-op, not a second transition")


def test_invalidate_still_re_captures() -> None:
    """disable() must not have been implemented by weakening invalidate()."""
    cfg = _cfg()
    t = _locked_tracker(cfg)
    check(t.invalidate("grating move") is True, "a shape change still invalidates")
    check(t.state is TemplateState.CAPTURING,
          f"and re-arms capture rather than falling back to fast (got {t.state})")


def test_the_toggle_reaches_the_tracker_both_ways() -> None:
    w = _worker(slow=True)
    PhaseStabilizationWorker._build_tracker(w)

    w._config.slow_correction = False
    _set_config(w, "1")
    check(w._tracker.state is TemplateState.OFF,
          f"selecting fast switches a running loop to the cold path (got {w._tracker.state})")

    w._config.slow_correction = True
    _set_config(w, "2")
    check(w._tracker.state is TemplateState.CAPTURING,
          f"and selecting slow re-arms the capture (got {w._tracker.state})")


def test_an_ordinary_edit_does_not_disturb_a_locked_template() -> None:
    """Nudging the gain must not cost a 5 s re-capture."""
    w = _worker(slow=True)
    PhaseStabilizationWorker._build_tracker(w)
    w._tracker.install(_template())
    check(w._tracker.state is TemplateState.LOCKED, "locked to begin with")

    w._config.loop_gain = 0.03          # a tuning change, not a mode change
    _set_config(w, "3")
    check(w._tracker.state is TemplateState.LOCKED,
          f"still locked after an unrelated config edit (got {w._tracker.state})")


# -- the capture counter ------------------------------------------------------------------
def _states(w) -> list[tuple]:
    """(state, captured) from every TemplateStateChanged the worker put on the wire."""
    return [(m.state, m.captured) for m in w.notified
            if type(m).__name__ == "TemplateStateChanged"]


def test_capture_progress_is_published_as_it_advances() -> None:
    """The 0/10 bug: progress was only published when a TEMPLATE appeared, so the whole
    run 1..9 was invisible and an abandoned run resetting to 0 was invisible too. A loop
    counting up perfectly and one restarting forever looked identical from the panel."""
    w = _worker(slow=True)
    PhaseStabilizationWorker._build_tracker(w)
    w.notified.clear()

    # Walk the tracker's run forward the way _capture does, publishing per frame.
    for n in range(1, 4):
        w._tracker._run = [None] * n
        PhaseStabilizationWorker._publish_template_state(w)
    check(_states(w) == [("capturing", 1), ("capturing", 2), ("capturing", 3)],
          f"each accepted trace is announced (got {_states(w)})")

    # A broken/abandoned run drops back to 0 -- which the operator must also see.
    w.notified.clear()
    w._tracker._run = []
    PhaseStabilizationWorker._publish_template_state(w)
    check(_states(w) == [("capturing", 0)],
          f"and so is a run collapsing back to zero (got {_states(w)})")


def test_an_unchanged_state_is_not_republished() -> None:
    """Called once per frame now, so silence while nothing moves is what keeps a LOCKED
    loop from putting an IPC message on the wire at the frame rate."""
    w = _worker(slow=True)
    PhaseStabilizationWorker._build_tracker(w)
    w.notified.clear()
    for _ in range(5):
        PhaseStabilizationWorker._publish_template_state(w)
    check(_states(w) == [], f"an unchanged state is published nothing (got {_states(w)})")


TESTS = [
    test_slow_is_the_default,
    test_the_default_survives_a_round_trip,
    test_starting_in_slow_mode_arms_the_capture,
    test_starting_in_fast_mode_does_not,
    test_switching_to_fast_goes_to_off_not_capturing,
    test_invalidate_still_re_captures,
    test_the_toggle_reaches_the_tracker_both_ways,
    test_an_ordinary_edit_does_not_disturb_a_locked_template,
    test_capture_progress_is_published_as_it_advances,
    test_an_unchanged_state_is_not_republished,
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
    print("all correction-mode checks passed")
