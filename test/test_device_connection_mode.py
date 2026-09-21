"""Connection-mode plumbing: the codec, the fallback policy, and the demotion notice.

Three things are checked, because each one has already failed in a way that hides:

  * the enum survives IPC *by identity*, not just by equality — a str-mixin enum
    compares equal even when it decodes to a bare string, so an equality test passes
    while ``is`` and ``match`` silently fail;
  * a DEVICE request whose hardware refuses ends up on the mock, and a MOCK request
    never touches the hardware at all;
  * a start that raises still answers. It used to not: ThreadedWorker dispatches the
    start through a runner whose error handler only logs, so a raising ``_start()``
    sent no reply and left the handle at BUSY for the life of the app.

Hand-rolled runner, like the rest of test/. Run it directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from base_core.framework.app.app_message import AppMessage, MessageLevel  # noqa: E402
from base_core.framework.events.event_bus import EventBus  # noqa: E402
from base_core.ipc import codec  # noqa: E402
from base_core.ipc.connection_mode import ConnectionMode  # noqa: E402
from base_core.ipc.device_worker import DeviceWorkerMixin  # noqa: E402
from base_core.ipc.device_worker_handle import (  # noqa: E402
    DeviceConnectionModeChanged,
    DeviceHandleMixin,
)
from base_core.ipc.message import ErrorReply, OKReply  # noqa: E402
from base_core.ipc.worker import BaseWorker  # noqa: E402
from base_core.ipc.worker_handle import BaseWorkerHandle  # noqa: E402
from base_core.ipc.worker_messages import StartWorker, WorkerStartedReply  # noqa: E402

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"ok    {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        _failures.append(name)


# --- doubles ---------------------------------------------------------------

class FakeConnector:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, msg) -> None:
        self.sent.append(msg)


class Worker(DeviceWorkerMixin, BaseWorker):
    """Bare BaseWorker, not ThreadedWorker: no runner, so replies land synchronously."""

    def __init__(self, *, hardware_ok: bool, mock_ok: bool = True) -> None:
        super().__init__("probe", EventBus(), FakeConnector())
        self._hardware_ok = hardware_ok
        self._mock_ok = mock_ok
        self.opened: list[str] = []
        self.driver = None

    def _setup(self) -> None: ...
    def _pause(self) -> None: ...
    def _resume(self) -> None: ...
    def _stop(self) -> None: ...

    def _start(self) -> None:
        self.driver = self._open_device()

    def _connect(self):
        self.opened.append("device")
        if not self._hardware_ok:
            raise RuntimeError("no COM4")
        return "real-driver"

    def _connect_mock(self):
        self.opened.append("mock")
        if not self._mock_ok:
            raise RuntimeError("the mock itself is broken")
        return "mock-driver"


class PlainWorker(BaseWorker):
    """A worker that fronts no hardware and knows nothing about connection modes."""

    def __init__(self) -> None:
        super().__init__("plain", EventBus(), FakeConnector())
        self.started = False

    def _setup(self) -> None: ...
    def _pause(self) -> None: ...
    def _resume(self) -> None: ...
    def _stop(self) -> None: ...

    def _start(self) -> None:
        self.started = True


class Handle(DeviceHandleMixin, BaseWorkerHandle):
    pass


def _start(worker: BaseWorker, mode: ConnectionMode) -> object:
    msg = StartWorker(worker_id=worker.worker_id, mode=mode)
    worker._on_start_cmd(msg)
    return worker._connector.sent[-1]


# --- cases -----------------------------------------------------------------

def test_enum_survives_ipc_by_identity() -> None:
    for mode in ConnectionMode:
        got = codec.decode(codec.encode(StartWorker(worker_id="w", mode=mode)))
        check(f"StartWorker mode={mode.value} decodes to the member itself",
              got.mode is mode, f"got {got.mode!r} ({type(got.mode).__name__})")

    reply = codec.decode(codec.encode(
        WorkerStartedReply(request_id="r", mode=ConnectionMode.MOCK, reason="boom")))
    check("the reply's mode decodes by identity", reply.mode is ConnectionMode.MOCK)
    check("the demotion reason survives", reply.reason == "boom")
    check("WorkerStartedReply is still an OKReply", isinstance(reply, OKReply))


def test_hardware_is_used_when_it_answers() -> None:
    w = Worker(hardware_ok=True)
    reply = _start(w, ConnectionMode.DEVICE)
    check("a working device is connected", w.driver == "real-driver")
    check("the mock is never built", w.opened == ["device"], f"got {w.opened}")
    check("the reply reports DEVICE", reply.mode is ConnectionMode.DEVICE)
    check("no reason is given when nothing went wrong", reply.reason == "")


def test_failed_hardware_demotes_to_the_mock() -> None:
    w = Worker(hardware_ok=False)
    reply = _start(w, ConnectionMode.DEVICE)
    check("the mock is connected instead", w.driver == "mock-driver")
    check("the device was tried first", w.opened == ["device", "mock"], f"got {w.opened}")
    check("the reply reports MOCK", reply.mode is ConnectionMode.MOCK)
    check("the reply carries the failure text", "no COM4" in reply.reason, reply.reason)


def test_a_mock_request_never_touches_the_hardware() -> None:
    # The point of asking for MOCK explicitly: a device that IS present stays untouched.
    w = Worker(hardware_ok=True)
    reply = _start(w, ConnectionMode.MOCK)
    check("only the mock is opened", w.opened == ["mock"], f"got {w.opened}")
    check("the reply reports MOCK", reply.mode is ConnectionMode.MOCK)


def test_a_broken_mock_answers_with_an_error() -> None:
    # A failing mock is a bug in the mock, not a fact about the lab. It must not be
    # swallowed — an unanswered start pins the handle at BUSY forever.
    w = Worker(hardware_ok=False, mock_ok=False)
    reply = _start(w, ConnectionMode.DEVICE)
    check("the start is answered rather than dropped", isinstance(reply, ErrorReply),
          f"got {type(reply).__name__}")
    check("the error names the cause", "mock itself is broken" in reply.error, reply.error)


def test_a_non_device_worker_is_untouched() -> None:
    w = PlainWorker()
    reply = _start(w, ConnectionMode.NONE)
    check("it starts normally", w.started)
    check("it reports NONE", reply.mode is ConnectionMode.NONE)


def test_the_handle_records_and_announces_the_demotion() -> None:
    bus = EventBus()
    modes: list[DeviceConnectionModeChanged] = []
    messages: list[AppMessage] = []
    bus.subscribe(DeviceConnectionModeChanged, modes.append)
    bus.subscribe(AppMessage, messages.append)

    handle = Handle("scope", bus)
    handle._on_start_reply(WorkerStartedReply(
        mode=ConnectionMode.MOCK, reason="VisaIOError: no instrument"))

    check("the handle records MOCK", handle.connection_mode is ConnectionMode.MOCK,
          f"got {handle.connection_mode!r}")
    check("a mode change is published", len(modes) == 1, f"got {len(modes)}")
    check("it is flagged as a demotion", bool(modes) and modes[-1].demoted)
    check("the operator gets a warning", bool(messages)
          and messages[-1].level == MessageLevel.WARNING,
          f"got {messages[-1:]}")
    check("the warning says why", bool(messages) and "no instrument" in messages[-1].text,
          f"got {messages[-1:]}")


def test_a_deliberate_mock_is_announced_but_not_a_demotion() -> None:
    bus = EventBus()
    modes: list[DeviceConnectionModeChanged] = []
    bus.subscribe(DeviceConnectionModeChanged, modes.append)

    handle = Handle("scope", bus)
    handle.requested_mode = ConnectionMode.MOCK
    handle._on_start_reply(WorkerStartedReply(mode=ConnectionMode.MOCK))

    check("asking for a mock still announces it", len(modes) == 1)
    check("but it is not a demotion", bool(modes) and not modes[-1].demoted)


def test_hardware_start_is_silent() -> None:
    bus = EventBus()
    messages: list[AppMessage] = []
    bus.subscribe(AppMessage, messages.append)

    handle = Handle("scope", bus)
    handle._on_start_reply(WorkerStartedReply(mode=ConnectionMode.DEVICE))
    check("a real connection raises no banner", messages == [], f"got {messages}")
    check("the handle records DEVICE", handle.connection_mode is ConnectionMode.DEVICE)


def test_stopping_forgets_the_mode() -> None:
    # A stopped worker is connected to nothing; a stale MOCK badge would be a lie.
    handle = Handle("scope", EventBus())
    handle._on_start_reply(WorkerStartedReply(mode=ConnectionMode.MOCK))
    handle._on_stop_reply(OKReply())
    check("the mode is cleared on stop", handle.connection_mode is None,
          f"got {handle.connection_mode!r}")


def main() -> int:
    for fn in (
        test_enum_survives_ipc_by_identity,
        test_hardware_is_used_when_it_answers,
        test_failed_hardware_demotes_to_the_mock,
        test_a_mock_request_never_touches_the_hardware,
        test_a_broken_mock_answers_with_an_error,
        test_a_non_device_worker_is_untouched,
        test_the_handle_records_and_announces_the_demotion,
        test_a_deliberate_mock_is_announced_but_not_a_demotion,
        test_hardware_start_is_silent,
        test_stopping_forgets_the_mode,
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
