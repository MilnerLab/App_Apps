"""The point of the whole exercise: every device comes up on a machine with no rig.

Before connection modes, ``ControlReadoutProcess.setup()`` connected the ESP301 and
the XPS eagerly. A missing COM4 raised there, aborted setup, and took every worker in
the subprocess down with it — including the ones on entirely different transports. So
on a development machine nothing in control-readout ran at all.

This boots the real process object with no hardware anywhere and asserts that all six
of its workers start, that each reports MOCK rather than failing, and that they are
then actually usable. It also checks the two cases that keep a mock honest: asking for
MOCK never touches hardware, and a device that IS present is not replaced.

Hand-rolled runner, like the rest of test/.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from base_core.framework.events.event_bus import EventBus  # noqa: E402
from base_core.ipc.connection_mode import ConnectionMode  # noqa: E402
from base_core.ipc.message import ErrorReply  # noqa: E402
from base_core.ipc.worker_messages import StartWorker  # noqa: E402
from control_readout.control_readout_process import ControlReadoutProcess  # noqa: E402
from control_readout.esp_301.fms300pp import spec as fms300pp_spec  # noqa: E402

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"ok    {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        _failures.append(name)


class FakeConnector:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, msg) -> None:
        self.sent.append(msg)


def _boot() -> ControlReadoutProcess:
    p = ControlReadoutProcess.__new__(ControlReadoutProcess)
    p.bus = EventBus()
    p.connector = FakeConnector()
    p._buffer_classes = {}
    p._buffers = {}
    p._workers = {}
    p.setup()
    return p


def _start(process: ControlReadoutProcess, worker, mode: ConnectionMode):
    before = len(process.connector.sent)
    msg = StartWorker(worker_id=worker.worker_id, mode=mode)
    worker._on_start_cmd(msg)
    # ThreadedWorker dispatches the start onto its own runner, so wait for the reply.
    runner = getattr(worker, "_runner", None)
    if runner is not None:
        deadline = __import__("time").monotonic() + 30.0
        while len(process.connector.sent) == before:
            if __import__("time").monotonic() > deadline:
                return None
            __import__("time").sleep(0.01)
    return process.connector.sent[-1]


def test_setup_survives_with_no_hardware() -> None:
    try:
        p = _boot()
    except Exception as exc:
        check("setup() survives a machine with no rig", False, f"{type(exc).__name__}: {exc}")
        return
    check("setup() survives a machine with no rig", True)
    expected = {"rotator", "rgv100bl", "fms300pp", "mfacc", "uts150cc",
                "picomotor", "servo_shutter"}
    check("every device worker is registered", set(p._workers) == expected,
          f"missing {expected - set(p._workers)}, extra {set(p._workers) - expected}")
    return p


#: The servo shutter is the one device with no hardware driver to fail. Its "real" path
#: prompts a human to block the arm, so it connects successfully on a bare machine and
#: honestly reports DEVICE. It becomes a genuine mock the day D16 lands the servos.
NEVER_DEMOTES = {"servo_shutter"}


def test_every_worker_starts_on_a_mock() -> None:
    p = _boot()
    for worker_id, worker in p._workers.items():
        reply = _start(p, worker, ConnectionMode.DEVICE)
        if reply is None:
            check(f"{worker_id} answers its start", False, "no reply within 30s")
            continue
        if isinstance(reply, ErrorReply):
            check(f"{worker_id} starts", False, f"ErrorReply: {reply.error}")
            continue
        expected = (ConnectionMode.DEVICE if worker_id in NEVER_DEMOTES
                    else ConnectionMode.MOCK)
        check(f"{worker_id} starts and reports {expected.value.upper()}",
              reply.mode is expected, f"got {reply.mode!r}")


def test_a_mocked_stage_is_actually_usable() -> None:
    # Coming up is not the same as working. A panel the operator cannot drive is only
    # marginally better than one that never appeared.
    p = _boot()
    worker = p._workers["fms300pp"]
    _start(p, worker, ConnectionMode.MOCK)
    stage = worker._stage
    check("the stage attached", stage is not None)
    if stage is None:
        return
    stage.home()
    check("home leaves it at zero", stage.position() == 0.0, f"got {stage.position()}")
    stage.move_to(12.5)
    check("it moves where it is told", stage.position() == 12.5, f"got {stage.position()}")
    stage.move_to(-100.0)
    # Asserted against the stage's own StageSpec, not a literal. The two used to
    # disagree: the mock clamped to the 0..300 mm datasheet travel while the soft limits
    # the application commands against are -9.5..290.5 mm, so a mocked move to a legal
    # negative position was silently pinned to 0 and reported as done.
    check("and stops at the soft limit, in the frame the application commands in",
          stage.position() == fms300pp_spec.SPEC.limit_min,
          f"got {stage.position()}, expected {fms300pp_spec.SPEC.limit_min} — a mock "
          f"that drives through a hard stop teaches the operator a habit the real stage "
          f"punishes, and one that stops somewhere else teaches a wrong reach")


def test_the_three_esp301_stages_share_one_mock_box() -> None:
    # The real ESP301 is one controller carrying three axes on one serial port. A mock
    # that gave each stage its own box would not behave like the thing it stands for.
    p = _boot()
    controllers = []
    for worker_id in ("fms300pp", "mfacc", "uts150cc"):
        worker = p._workers[worker_id]
        _start(p, worker, ConnectionMode.MOCK)
        controllers.append(worker._stage.controller)
    check("all three ESP301 stages share one mock controller",
          controllers[0] is controllers[1] is controllers[2],
          f"got {[id(c) for c in controllers]}")
    check("and each is a distinct axis",
          len({w.address for w in (p._workers[i]._stage for i in
                                   ("fms300pp", "mfacc", "uts150cc"))}) == 3)


def test_a_working_device_is_not_replaced() -> None:
    # The fallback must not be greedy: hardware that answers stays connected.
    p = _boot()
    worker = p._workers["picomotor"]

    class _FakeDriver:
        def open(self): ...
        def close(self): ...

    worker._connect = lambda: _FakeDriver()
    reply = _start(p, worker, ConnectionMode.DEVICE)
    check("a device that answers is kept", reply.mode is ConnectionMode.DEVICE,
          f"got {reply.mode!r}")
    check("and the mock was never built", isinstance(worker._driver, _FakeDriver),
          f"got {type(worker._driver).__name__}")


def main() -> int:
    for fn in (
        test_setup_survives_with_no_hardware,
        test_every_worker_starts_on_a_mock,
        test_a_mocked_stage_is_actually_usable,
        test_the_three_esp301_stages_share_one_mock_box,
        test_a_working_device_is_not_replaced,
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
