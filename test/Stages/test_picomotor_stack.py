"""Picomotor stack tests: config, mock driver, the worker, and the handle's coercion.

Deliberately does NOT test the real driver — that needs the controller, and a mock has
none of the physics. What it does cover is everything that would otherwise only be
exercised by plugging in: that mock and real expose the same method surface (so the
mock stays useful for UI work), that the counter semantics are open-loop throughout,
and that the mirror mapping is declared and flagged.

Hand-rolled runner, like the rest of test/.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from base_core.ipc.connection_mode import ConnectionMode  # noqa: E402
from control_readout.picomotor.config import (  # noqa: E402
    DEFAULT_MIRRORS,
    PicomotorConfig,
)
from control_readout.picomotor.mock_driver import MockPicomotor  # noqa: E402
from control_readout.picomotor.mock_params import MockPicomotorParams  # noqa: E402
from control_readout.picomotor.picomotor_driver import Picomotor8742  # noqa: E402

_failures: list[str] = []

#: Instant, so the tests assert behaviour rather than wait on simulated travel.
INSTANT = MockPicomotorParams(step_time_s=0.0)


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"ok    {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        _failures.append(name)


def _mock() -> MockPicomotor:
    d = MockPicomotor(PicomotorConfig(), INSTANT)
    d.open()
    return d


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
    d = _mock()
    check("counter starts at zero", d.position(3) == 0)
    d.move_by(3, 50)
    d.move_by(3, -20)
    check("relative moves accumulate", d.position(3) == 30, f"got {d.position(3)}")
    d.move_to(3, -5)
    check("absolute move sets the counter", d.position(3) == -5, f"got {d.position(3)}")
    d.zero(3)
    check("zero re-references the axis", d.position(3) == 0)
    d.move_by(1, 7)
    d.zero(3)
    check("zero touches only its own axis", d.position(1) == 7, f"got {d.position(1)}")
    check("is_moving is False once a move has returned", d.is_moving(3) is False)
    d.close()


def test_mirror_mapping_is_declared_and_flagged() -> None:
    axes = [a for m in DEFAULT_MIRRORS for a in (m.yaw_axis, m.pitch_axis)]
    check("every axis appears exactly once", sorted(axes) == [1, 2, 3, 4], f"got {axes}")
    critical = [m for m in DEFAULT_MIRRORS if m.critical]
    check("exactly one mirror is marked critical", len(critical) == 1)
    check("motor 3 is the critical yaw axis",
          bool(critical) and critical[0].yaw_axis == 3,
          f"got {critical[0].yaw_axis if critical else None}")


def test_config_carries_no_mock_flag() -> None:
    # The config describes the 8742. Whether a mock stands in for it is a start-time
    # decision on the start message, not a property of the instrument.
    fields = set(PicomotorConfig().__dataclass_fields__)
    check("no mock flag on the hardware config", "mock" not in fields, f"got {fields}")


def test_worker_commands_reach_the_driver() -> None:
    # Drive the worker's handlers directly against a mock driver, without the IPC
    # plumbing: what matters here is that each message maps to the right driver call
    # and that every one of them reports the counter back.
    from control_readout.picomotor import picomotor_worker as pw
    from control_readout.picomotor.messages import QuerySteps, StepBy, StepTo, ZeroAxis

    driver = _mock()
    notified: list[tuple[int, int]] = []
    replies: list = []

    class _Stub(pw.PicomotorWorker):
        def __init__(self):            # bypass ThreadedWorker's constructor
            self._config = PicomotorConfig()
            self._driver = driver
            self._is_paused = False

        def _notify(self, msg):        notified.append((msg.axis, msg.total_steps))
        def _reply_ok(self, request):  replies.append("ok")
        def _reply(self, reply):       replies.append(reply)
        def _reply_error(self, request, error): replies.append(f"error: {error}")

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


def test_worker_falls_back_to_the_mock() -> None:
    """No 8742 on the network must cost a warning, not a dead panel."""
    from control_readout.picomotor.picomotor_worker import PicomotorWorker

    class _Stub(PicomotorWorker):
        def __init__(self):
            self._config = PicomotorConfig()
            self._driver = None
            self._is_paused = False
            self._worker_id = "picomotor"
            self._requested_mode = ConnectionMode.DEVICE
            self._demotion_reason = ""
            self._connection_mode = ConnectionMode.NONE

        def _connect(self):
            raise ConnectionRefusedError("no route to 10.1.137.239")

    w = _Stub()
    w._start()
    check("the mock is connected instead", isinstance(w._driver, MockPicomotor),
          f"got {type(w._driver).__name__}")
    check("the worker reports MOCK", w._connection_mode is ConnectionMode.MOCK)
    check("and says why", "no route" in w._demotion_reason, w._demotion_reason)


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
        test_mirror_mapping_is_declared_and_flagged,
        test_config_carries_no_mock_flag,
        test_worker_commands_reach_the_driver,
        test_worker_falls_back_to_the_mock,
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
