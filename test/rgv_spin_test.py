"""Continuous RGV rotation: the rate limits and the precedence between the three drivers.

Three things can command the half-wave plate -- the operator, the free-running spin, and
the phase/envelope control loop -- and they have a strict order: a manual command beats a
spin, a spin beats stabilization. What is checked here is that every step down that order
STOPS the thing it overrides before issuing anything, that a refused confirmation moves
nothing at all, and that the tracked angle goes unknown the moment the plate starts turning
(a relative move synthesised from a stale angle is a large blind jump).

Needs Qt for the view model, but no hardware and no event loop:

    python test/rgv_spin_test.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from base_core.framework.events import EventBus  # noqa: E402
from base_core.ipc.message import ErrorReply  # noqa: E402
from base_core.ipc.worker_handle import WorkerStatus  # noqa: E402
from base_core.math.enums import AngleUnit  # noqa: E402
from base_core.math.models import Angle  # noqa: E402
from base_qt.app.dispatcher import QtDispatcher  # noqa: E402
from base_qt.ui.app_message import MessageLevel  # noqa: E402
from control_readout.newport_xps.rgv100bl.messages import (  # noqa: E402
    HomeRGV,
    RotateRGVTo,
    SpinRGV,
    StopSpinRGV,
)
from control_readout.newport_xps.rgv100bl.rgv100bl_device import MAX_SPIN_DEG_S  # noqa: E402
from control_readout.newport_xps.rgv100bl.rgv100bl_worker import Rgv100blWorker  # noqa: E402

from app_apps.io.control_readout.rgv.events import (  # noqa: E402
    RequestRotateRGV,
    RgvSpinStateChanged,
)
from app_apps.io.control_readout.rgv.handler import RgvHandle  # noqa: E402
from app_apps.io.control_readout.rgv.ui.view_model import (  # noqa: E402
    DEG_PER_REV,
    MAX_SPIN_HZ,
    MIN_SPIN_HZ,
    RgvViewModel,
)

_fails: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        _fails.append(msg)


# -- fixtures ---------------------------------------------------------------------------
class _FakePhaseService:
    """Stands in for PhaseControlService: says a loop is running, records the stop."""

    def __init__(self, running: bool = True) -> None:
        self.running = running
        self.stops = 0

    @property
    def active_state(self) -> WorkerStatus:
        return WorkerStatus.RUNNING if self.running else WorkerStatus.NEW

    def stop_worker(self) -> None:
        self.stops += 1
        self.running = False


def _vm(stabilizing: bool = True):
    """A view model whose handle records the IPC messages instead of sending them."""
    bus = EventBus()
    handle = RgvHandle(bus=bus)
    sent: list[str] = []
    handle._request = lambda msg, cb=None, err=None: sent.append(type(msg).__name__)
    service = _FakePhaseService(stabilizing)
    vm = RgvViewModel(bus, QtDispatcher(), handle, service)
    # Both confirmations answer Yes and record what they were asked about; the refusal
    # cases install their own. The stabilization one also stops the loop, because that is
    # what the real dialog in ``RgvControls._confirm_move`` does -- the stop lives with the
    # dialog, so a stub that only returns True would not be testing the actual ordering.
    asked: list[tuple[str, str]] = []

    def confirm_stab(description: str) -> bool:
        asked.append(("stab", description))
        if vm.stabilization_running:
            vm.stop_stabilization()
        return True

    vm.confirm_move = confirm_stab
    vm.confirm_spin_override = lambda d: (asked.append(("spin", d)), True)[1]
    return vm, handle, sent, asked, service


def _worker():
    """A worker wired to a fake rotator, with @worker_thread dispatch made synchronous."""
    acts: list[tuple] = []

    class FakeRot:
        def spin(self, v):
            acts.append(("spin", v))

        def stop_spin(self):
            acts.append(("stop_spin",))

        def rotate(self, a):
            acts.append(("rotate", round(a.Deg, 6)))

        def home(self):
            acts.append(("home",))

        def abort(self):
            acts.append(("abort",))

        def stop(self):
            acts.append(("stop",))

        def angle(self):
            return Angle(3.0, AngleUnit.DEG)

    class InlineRunner:
        def run(self, fn, on_error=None):
            return fn()

    w = Rgv100blWorker.__new__(Rgv100blWorker)
    w._runner = InlineRunner()
    w._rotator = FakeRot()
    w._spinning = False
    notes: list[str] = []
    w._notify = lambda m: notes.append(type(m).__name__)
    w._reply_ok = lambda m: None
    w._reply_error = lambda m, e: acts.append(("error", e))
    return w, acts, notes


# -- rate limits ------------------------------------------------------------------------
def test_the_ceiling_is_the_stages_own() -> None:
    """2 rev/s is not a chosen number: it is the RGV100's 720 deg/s maximum."""
    check(MAX_SPIN_HZ * DEG_PER_REV == MAX_SPIN_DEG_S,
          f"{MAX_SPIN_HZ} rev/s == {MAX_SPIN_DEG_S} deg/s, the stage maximum")
    check(MIN_SPIN_HZ * DEG_PER_REV == 180.0,
          f"the {MIN_SPIN_HZ} rev/s floor is 180 deg/s, a quarter of the ceiling")


def test_rates_are_clamped_not_rejected() -> None:
    check(RgvViewModel._clamp_rate(0.01) == MIN_SPIN_HZ, "a too-slow rate clamps to the floor")
    check(RgvViewModel._clamp_rate(99.0) == MAX_SPIN_HZ, "a too-fast rate clamps to the ceiling")
    check(RgvViewModel._clamp_rate(1.25) == 1.25, "an in-range rate passes through")


def test_the_commanded_velocity_is_the_rate_in_deg_per_s() -> None:
    vm, handle, _sent, _asked, _svc = _vm(stabilizing=False)
    commanded: list[float] = []
    handle._request = lambda msg, cb=None, err=None: commanded.append(getattr(msg, "velocity_deg_s", None))
    vm.start_spin(0.5)
    check(commanded == [180.0], f"0.5 rev/s is commanded as 180 deg/s (got {commanded})")


# -- precedence: spin over stabilization ------------------------------------------------
def test_spin_stops_stabilization_first() -> None:
    vm, _handle, sent, asked, svc = _vm(stabilizing=True)
    vm.start_spin(1.5)
    check([k for k, _ in asked] == ["stab"], "starting a spin asks the stabilization dialog")
    check(svc.stops == 1, "stabilization was stopped")
    check(sent == ["SpinRGV"], f"exactly one spin command went out (got {sent})")


def test_a_refused_spin_does_nothing() -> None:
    vm, _handle, sent, _asked, svc = _vm(stabilizing=True)
    vm.confirm_move = lambda d: False
    vm.start_spin(1.5)
    check(sent == [], "a refused spin sends nothing")
    check(svc.stops == 0, "a refused spin leaves stabilization running")


# -- precedence: manual over spin -------------------------------------------------------
def test_a_manual_move_stops_the_spin_before_moving() -> None:
    vm, _handle, sent, asked, _svc = _vm(stabilizing=False)
    vm.start_spin(1.0)
    sent.clear()
    asked.clear()
    vm.move_absolute(12.0)
    check([k for k, _ in asked][0] == "spin", "the move asks the spin-override dialog first")
    check(sent == ["StopSpinRGV", "RotateRGVTo"],
          f"the spin is stopped BEFORE the move goes out (got {sent})")


def test_home_stops_the_spin_too() -> None:
    vm, _handle, sent, _asked, _svc = _vm(stabilizing=False)
    vm.start_spin(1.0)
    sent.clear()
    vm.home()
    check(sent == ["StopSpinRGV", "HomeRGV"], f"home overrides the spin as well (got {sent})")


def test_a_refused_override_moves_nothing() -> None:
    vm, _handle, sent, _asked, _svc = _vm(stabilizing=False)
    vm.start_spin(1.0)
    vm.confirm_spin_override = lambda d: False
    sent.clear()
    vm.move_absolute(30.0)
    vm.home()
    vm.move_relative(1.0)
    check(sent == [], f"cancelling leaves the plate spinning and untouched (got {sent})")


def test_re_rating_a_running_spin_does_not_ask_or_stop() -> None:
    vm, _handle, sent, asked, _svc = _vm(stabilizing=False)
    vm.start_spin(0.5)
    sent.clear()
    asked.clear()
    vm.start_spin(1.5)
    check(sent == ["SpinRGV"], f"a rate change is one command, no stop (got {sent})")
    check(asked == [], "a rate change asks nothing")


# -- the angle is unknown while turning -------------------------------------------------
def test_the_position_goes_unknown_the_moment_it_spins() -> None:
    vm, handle, _sent, _asked, _svc = _vm(stabilizing=False)
    vm._position = 10.0
    handle._current_angle = Angle(10.0, AngleUnit.DEG)
    vm.start_spin(0.5)
    check(vm.position is None, "the panel readout blanks rather than showing a stale angle")
    check(handle._current_angle is None, "the handle forgets the angle it can no longer track")


def test_a_correction_arriving_mid_spin_is_dropped_not_applied() -> None:
    """The dangerous case: a relative increment against a position nobody knows."""
    vm, handle, sent, _asked, _svc = _vm(stabilizing=False)
    vm.start_spin(0.5)
    sent.clear()
    handle._on_request_rotate(RequestRotateRGV(angle=Angle(0.7, AngleUnit.DEG)))
    check(sent == ["StopSpinRGV"],
          f"the spin is stopped and the increment discarded, not applied (got {sent})")


# -- worker: nothing leaves the plate turning -------------------------------------------
def test_the_worker_stops_a_spin_before_any_position_command() -> None:
    w, acts, _notes = _worker()
    Rgv100blWorker._on_spin(w, SpinRGV(velocity_deg_s=180.0))
    check(w._spinning and acts == [("spin", 180.0)], "the worker spins on command")

    acts.clear()
    Rgv100blWorker._on_rotate(w, RotateRGVTo(angle=Angle(10.0, AngleUnit.DEG)))
    check(acts[0] == ("stop_spin",) and acts[1][0] == "rotate",
          f"an absolute move ends the spin first (got {acts})")
    check(not w._spinning, "and the worker knows it is no longer spinning")

    Rgv100blWorker._on_spin(w, SpinRGV(velocity_deg_s=90.0))
    acts.clear()
    Rgv100blWorker._on_home(w, HomeRGV())
    check(acts == [("stop_spin",), ("home",)], f"home ends the spin first (got {acts})")


def test_the_lifecycle_never_leaves_it_spinning() -> None:
    w, acts, _notes = _worker()
    Rgv100blWorker._on_spin(w, SpinRGV(velocity_deg_s=90.0))
    acts.clear()
    Rgv100blWorker._pause(w)
    check(acts == [("stop_spin",), ("abort",)],
          f"pause ramps down before aborting -- no shock load (got {acts})")

    Rgv100blWorker._on_spin(w, SpinRGV(velocity_deg_s=90.0))
    acts.clear()
    Rgv100blWorker._stop(w)
    check(acts == [("stop_spin",), ("stop",)], f"stop ramps down too (got {acts})")
    check(w._rotator is None, "and releases the device")


def test_stopping_reports_the_angle_it_settled_at() -> None:
    w, _acts, notes = _worker()
    Rgv100blWorker._on_spin(w, SpinRGV(velocity_deg_s=90.0))
    notes.clear()
    Rgv100blWorker._on_stop_spin(w, StopSpinRGV())
    check(notes == ["RGVSpinStateUpdate", "RGVAngleUpdate"],
          f"the angle is republished only once the plate has stopped (got {notes})")

def test_a_controller_rejection_rolls_the_spin_back() -> None:
    """The failure that looks exactly like "spin does not work".

    ``spin()`` announces optimistically, so a controller that refuses the command (a
    GROUP1 declared SingleAxis rather than SpindleAxis is the likely cause) would
    otherwise leave the app believing a stationary plate is turning: the toggle stays on
    "Stop spin", the angle stays unknown, and -- because a spin outranks the control loop
    -- every stabilization correction is silently dropped for as long as the app is up.
    """
    bus = EventBus()
    handle = RgvHandle(bus=bus)
    errbacks: list = []
    handle._request = lambda msg, cb=None, err=None: errbacks.append((msg, err))
    seen: list[RgvSpinStateChanged] = []
    bus.subscribe(RgvSpinStateChanged, seen.append)

    handle.spin(180.0)
    check(handle.spinning, "announced as spinning while the request is in flight")
    _msg, on_error = errbacks[0]
    check(on_error is not None, "the spin request carries an error callback at all")

    seen.clear()
    on_error(ErrorReply(request_id="1", error="GROUP1 is not a SpindleAxis"))
    check(not handle.spinning, "a refused spin leaves the handle NOT spinning")
    check([e.spinning for e in seen] == [False],
          f"and republishes so the panel toggle springs back (got {seen})")
    check("SpindleAxis" in seen[0].error,
          f"carrying the controller's own reason (got {seen[0].error!r})")
    check(any(type(m).__name__ == "GetCurrentRGVAngle" for m, _ in errbacks),
          "and re-reads the angle -- the plate never moved, so it is still valid")


def test_a_rejection_reaches_the_operator() -> None:
    vm, _handle, _sent, _service, _asked = _vm()
    said: list[tuple[str, object]] = []
    vm._msg = lambda text, level=None: said.append((text, level))
    # Called through __wrapped__: the handler is @ui_thread, so invoking it directly would
    # only queue the work onto an event loop this test never runs.
    type(vm)._on_spin_state.__wrapped__(
        vm, RgvSpinStateChanged(spinning=False, velocity_deg_s=0.0,
                                error="GROUP1 is not a SpindleAxis"))
    check(any("refused" in t and "SpindleAxis" in t for t, _ in said),
          f"the panel says the spin was refused and why (got {said})")
    check(said and said[0][1] is MessageLevel.ERROR, "at ERROR, not a quiet INFO")


TESTS = [
    test_the_ceiling_is_the_stages_own,
    test_rates_are_clamped_not_rejected,
    test_the_commanded_velocity_is_the_rate_in_deg_per_s,
    test_spin_stops_stabilization_first,
    test_a_refused_spin_does_nothing,
    test_a_manual_move_stops_the_spin_before_moving,
    test_home_stops_the_spin_too,
    test_a_refused_override_moves_nothing,
    test_re_rating_a_running_spin_does_not_ask_or_stop,
    test_the_position_goes_unknown_the_moment_it_spins,
    test_a_correction_arriving_mid_spin_is_dropped_not_applied,
    test_the_worker_stops_a_spin_before_any_position_command,
    test_the_lifecycle_never_leaves_it_spinning,
    test_stopping_reports_the_angle_it_settled_at,
    test_a_controller_rejection_rolls_the_spin_back,
    test_a_rejection_reaches_the_operator,
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
    print("all RGV spin checks passed")
