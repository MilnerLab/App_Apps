"""Picomotor stack tests: config, mock driver, and the worker's command surface.

Deliberately does NOT test the real driver — that needs the controller, and a mock
has none of the physics. What it does cover is everything that would otherwise only
be exercised by plugging in: that mock and real expose the same method surface (so
the mock stays useful for UI work), that the counter semantics are open-loop
throughout, and that ``from_env`` really does let the rig pick the real driver
without a source edit.

Hand-rolled runner, like the rest of test/.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_readout.picomotor.config import (  # noqa: E402
    CONN_ENV_VAR,
    DEFAULT_MIRRORS,
    MOCK_ENV_VAR,
    PicomotorConfig,
)
from control_readout.picomotor.mock_driver import MockPicomotor  # noqa: E402
from control_readout.picomotor.picomotor_driver import Picomotor8742  # noqa: E402

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"ok    {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        _failures.append(name)


def test_mock_and_real_expose_the_same_surface() -> None:
    # The mock is only useful for UI work if the UI cannot tell them apart. A method
    # added to one and not the other is exactly the drift this catches.
    wanted = {"open", "close", "move_by", "move_to", "position", "zero", "is_moving",
              "wait_for_stop"}
    mock = {n for n in dir(MockPicomotor) if not n.startswith("_")}
    real = {n for n in dir(Picomotor8742) if not n.startswith("_")}
    check("mock exposes the full surface", wanted <= mock, f"missing {wanted - mock}")
    check("real exposes the full surface", wanted <= real, f"missing {wanted - real}")
    check("surfaces match exactly", mock == real, f"symmetric difference {mock ^ real}")


def test_mock_counter_semantics() -> None:
    d = MockPicomotor(PicomotorConfig())
    d.open()
    check("counter starts at zero", d.position(3) == 0)
    d.move_by(3, 50)
    d.move_by(3, -20)
    check("relative moves accumulate", d.position(3) == 30, f"got {d.position(3)}")
    d.move_to(3, -5)
    check("absolute move sets the counter", d.position(3) == -5, f"got {d.position(3)}")
    d.zero(3)
    check("zero re-references without moving others", d.position(3) == 0)
    d.move_by(1, 7)
    d.zero(3)
    check("zero touches only its own axis", d.position(1) == 7, f"got {d.position(1)}")
    check("is_moving is False for the mock", d.is_moving(3) is False)
    d.close()


def test_config_from_env() -> None:
    saved = {k: os.environ.get(k) for k in (MOCK_ENV_VAR, CONN_ENV_VAR)}
    try:
        os.environ.pop(MOCK_ENV_VAR, None)
        os.environ.pop(CONN_ENV_VAR, None)
        check("defaults to mock", PicomotorConfig.from_env().mock is True)
        check("defaults to usb", PicomotorConfig.from_env().transport == "usb")

        os.environ[MOCK_ENV_VAR] = "0"
        check("PICOMOTOR_MOCK=0 selects the real driver",
              PicomotorConfig.from_env().mock is False)
        os.environ[MOCK_ENV_VAR] = "true"
        check("PICOMOTOR_MOCK=true selects the mock",
              PicomotorConfig.from_env().mock is True)

        os.environ[CONN_ENV_VAR] = "10.1.137.239"
        cfg = PicomotorConfig.from_env()
        check("a dotted conn is treated as network",
              cfg.transport == "network" and cfg.host == "10.1.137.239")
        os.environ[CONN_ENV_VAR] = "1"
        cfg = PicomotorConfig.from_env()
        check("a bare index is treated as usb",
              cfg.transport == "usb" and cfg.host == "1")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_mirror_mapping_is_declared_and_flagged() -> None:
    axes = [a for m in DEFAULT_MIRRORS for a in (m.yaw_axis, m.pitch_axis)]
    check("every axis appears exactly once", sorted(axes) == [1, 2, 3, 4], f"got {axes}")
    critical = [m for m in DEFAULT_MIRRORS if m.critical]
    check("exactly one mirror is marked critical", len(critical) == 1)
    check("motor 3 is the critical yaw axis",
          bool(critical) and critical[0].yaw_axis == 3,
          f"got {critical[0].yaw_axis if critical else None}")


def test_worker_commands_reach_the_driver() -> None:
    # Drive the worker's handlers directly against a mock driver, without the IPC
    # plumbing: what matters here is that each message maps to the right driver call
    # and that every one of them reports the counter back.
    from control_readout.picomotor import picomotor_worker as pw

    driver = MockPicomotor(PicomotorConfig())
    notified: list[tuple[int, int]] = []
    replies: list[str] = []

    class _Stub(pw.PicomotorWorker):
        def __init__(self):            # bypass ThreadedWorker's constructor
            self._config = PicomotorConfig()
            self._driver = driver
            self._is_paused = False

        def _notify(self, msg):        notified.append((msg.axis, msg.total_steps))
        def _reply_ok(self, request):  replies.append("ok")
        def _reply(self, reply):       replies.append(reply)
        def _reply_error(self, request, error): replies.append(f"error: {error}")

    from control_readout.picomotor.messages import QuerySteps, StepBy, StepTo, ZeroAxis

    w = _Stub()
    # The handlers are wrapped in @worker_thread; call the underlying functions.
    pw.PicomotorWorker._on_step_by.__wrapped__(w, StepBy(axis=2, steps=15))
    check("StepBy moves and reports", driver.position(2) == 15 and notified[-1] == (2, 15),
          f"pos={driver.position(2)} notified={notified[-1:]}")

    pw.PicomotorWorker._on_step_to.__wrapped__(w, StepTo(axis=2, steps=-4))
    check("StepTo moves and reports", driver.position(2) == -4 and notified[-1] == (2, -4),
          f"pos={driver.position(2)}")

    pw.PicomotorWorker._on_zero_axis.__wrapped__(w, ZeroAxis(axis=2))
    check("ZeroAxis re-references and reports",
          driver.position(2) == 0 and notified[-1] == (2, 0))

    driver.move_by(4, 9)
    pw.PicomotorWorker._on_query_steps.__wrapped__(w, QuerySteps(axes=(2, 4)))
    reply = replies[-1]
    check("QuerySteps reads without moving",
          getattr(reply, "steps", None) == {2: 0, 4: 9}, f"got {reply}")

    w._is_paused = True
    before = driver.position(2)
    pw.PicomotorWorker._on_step_by.__wrapped__(w, StepBy(axis=2, steps=99))
    check("a paused worker refuses to move",
          driver.position(2) == before and str(replies[-1]).startswith("error:"),
          f"pos={driver.position(2)} reply={replies[-1]}")


def test_worker_waits_for_motion_before_reading_the_counter() -> None:
    """Regression: the readout was one command stale on real hardware.

    The 8742's move commands return when accepted, not when the motion completes, so
    a ``position()`` taken immediately reports the pre-move count. Modelled here with
    a driver whose counter only settles once ``wait_for_stop`` has been called — the
    mock cannot show this, because mock motion is instantaneous.
    """
    from control_readout.picomotor import picomotor_worker as pw
    from control_readout.picomotor.messages import StepBy

    class LaggyDriver:
        def __init__(self):
            self.committed = 0
            self.pending = 0
        def move_by(self, axis, steps): self.pending = self.committed + steps
        def wait_for_stop(self, axis, timeout_s=30.0):
            self.committed = self.pending
            return True
        def position(self, axis): return self.committed

    driver = LaggyDriver()
    notified: list[tuple[int, int]] = []

    class _Stub(pw.PicomotorWorker):
        def __init__(self):
            self._config = PicomotorConfig()
            self._driver = driver
            self._is_paused = False
        def _notify(self, msg): notified.append((msg.axis, msg.total_steps))
        def _reply_ok(self, request): pass
        def _reply_error(self, request, error): notified.append(("error", error))

    pw.PicomotorWorker._on_step_by.__wrapped__(_Stub(), StepBy(axis=1, steps=25))
    check("worker reports the settled counter, not the stale one",
          notified == [(1, 25)], f"got {notified}")


def test_handle_coerces_json_string_axis_keys() -> None:
    """A StepsReply arriving over IPC has STRING keys — regression for a real bug.

    Caught end-to-end against the controller, not by the mock: the mock path never
    crosses the process boundary, so the dict came back with int keys and everything
    looked fine. Over the JSON codec the axes arrive as '1'..'4' and every int lookup
    in the UI misses, leaving the readouts blank while the counters are known.
    """
    from base_core.framework.events.event_bus import EventBus
    from control_readout.picomotor.messages import StepsReply

    from app_apps.io.control_readout.picomotor.events import PicomotorStepsChanged
    from app_apps.io.control_readout.picomotor.handler import PicomotorHandle

    bus = EventBus()
    published: list[dict] = []
    bus.subscribe(PicomotorStepsChanged, lambda e: published.append(dict(e.steps)))

    handle = PicomotorHandle(bus=bus)
    handle._on_steps_reply(StepsReply(steps={"1": 310, "3": -7}))
    check("handle coerces string axis keys to int",
          handle.steps == {1: 310, 3: -7}, f"got {handle.steps}")
    check("the published event carries int keys",
          published and published[-1] == {1: 310, 3: -7}, f"got {published[-1:]}")


def main() -> int:
    for fn in (
        test_mock_and_real_expose_the_same_surface,
        test_mock_counter_semantics,
        test_config_from_env,
        test_mirror_mapping_is_declared_and_flagged,
        test_worker_commands_reach_the_driver,
        test_worker_waits_for_motion_before_reading_the_counter,
        test_handle_coerces_json_string_axis_keys,
    ):
        fn()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} — " + ", ".join(_failures))
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
