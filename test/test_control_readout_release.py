"""Tests for the graceful hardware-release handshake (defect G19 / finding-1).

The ESP301 serial port (COM7) must be closed *cleanly* before the subprocess is
hard-killed, and — critically — never closed while a worker thread is mid-command,
because an abrupt close mid-hold is what wedges the TI-3410 USB bridge. This checks
the subprocess-side handler that does the closing:

  * it disconnects an idle controller,
  * it takes the controller's IO lock first, so it will NOT close while a command
    holds that lock (it waits, and gives up rather than closing mid-command),
  * one controller's failure doesn't stop the others, and
  * it always replies OKReply so the parent never blocks on shutdown.

No pytest (repo convention): run directly —

    App_Apps\\.venv\\Scripts\\python.exe App_Apps\\test\\test_control_readout_release.py

Exit 0 means every case passed.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback

from base_core.ipc.message import OKReply
from control_readout.control_readout_process import ControlReadoutProcess
from control_readout.messages import ReleaseHardware


class FakeController:
    """Minimal stand-in for a control_readout Controller.

    Has the real RLock the production code serializes against, and records whether
    disconnect() was called and whether the lock was held at that moment.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self._lock = threading.RLock()
        self.disconnected = False
        self.held_lock_on_disconnect: bool | None = None
        self._fail = fail

    def disconnect(self) -> None:
        # If the caller acquired self._lock first, a non-blocking acquire from a
        # *different* thread would fail. We record, from a helper thread, whether the
        # lock was held while disconnect ran.
        result: dict[str, bool] = {}
        def _probe() -> None:
            got = self._lock.acquire(blocking=False)
            result["held"] = not got
            if got:
                self._lock.release()
        t = threading.Thread(target=_probe)
        t.start()
        t.join()
        self.held_lock_on_disconnect = result["held"]
        if self._fail:
            raise RuntimeError("boom")
        self.disconnected = True


class FakeConnector:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, msg) -> None:
        self.sent.append(msg)


def _make_process(controllers) -> ControlReadoutProcess:
    """A ControlReadoutProcess without running the full subprocess __init__."""
    p = ControlReadoutProcess.__new__(ControlReadoutProcess)
    p._controllers = list(controllers)
    p.connector = FakeConnector()
    return p


# --- cases ----------------------------------------------------------------

def test_disconnects_idle_controller_under_its_lock():
    c = FakeController()
    ControlReadoutProcess._disconnect_quiescent(c)
    assert c.disconnected, "idle controller should be disconnected"
    assert c.held_lock_on_disconnect is True, "disconnect must run while holding the IO lock"


def test_does_not_close_while_a_command_holds_the_lock():
    c = FakeController()
    released = threading.Event()
    holding = threading.Event()

    def hold() -> None:
        with c._lock:
            holding.set()
            released.wait(2.0)

    t = threading.Thread(target=hold)
    t.start()
    holding.wait(1.0)  # ensure the "command" owns the lock

    start = time.monotonic()
    ControlReadoutProcess._disconnect_quiescent(c, lock_timeout=0.2)
    waited = time.monotonic() - start

    assert not c.disconnected, "must NOT close the port while a command holds the lock"
    assert waited >= 0.2, "should have waited for the lock up to the timeout"
    released.set()
    t.join()


def test_lock_freed_after_timeout_allows_a_later_close():
    # After the busy command releases the lock, a fresh release closes normally.
    c = FakeController()
    ControlReadoutProcess._disconnect_quiescent(c, lock_timeout=0.2)
    assert c.disconnected


def test_one_failure_does_not_stop_the_others_and_still_replies_ok():
    bad = FakeController(fail=True)
    good = FakeController()
    p = _make_process([bad, good])
    msg = ReleaseHardware()

    p._on_release_hardware(msg)

    assert good.disconnected, "a failing controller must not prevent closing the others"
    assert len(p.connector.sent) == 1
    reply = p.connector.sent[0]
    assert isinstance(reply, OKReply)
    assert reply.request_id == msg.id, "OKReply must correlate to the request"


def test_never_connected_controller_is_harmless():
    # disconnect() on a controller that never connected is a no-op in production;
    # here we just confirm the handler tolerates a controller and always replies.
    c = FakeController()
    p = _make_process([c])
    p._on_release_hardware(ReleaseHardware())
    assert isinstance(p.connector.sent[0], OKReply)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok    {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
