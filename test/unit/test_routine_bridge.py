"""Unit tests for the linear-routine async->sync bridge (R.1).

These exercise the load-bearing behavior: a thread blocked in a bridge primitive is woken
by an event/reply published from a *different* thread — the same pattern the real IPC
reader thread uses — and cancellation/timeouts fire at bounded latency. No subprocess, no
hardware, no UI; just the real EventBus.
"""
import os
import sys
import threading
import time
import unittest
from dataclasses import dataclass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.routines.linear.bridge import (
    await_event,
    await_reply,
    cancellable_sleep,
)
from app_apps.routines.linear.cancel import (
    CancelToken,
    RoutineCancelled,
    RoutineTimeout,
)
from base_core.framework.events.event_bus import EventBus


@dataclass(frozen=True)
class DummyDone:
    axis: int


@dataclass(frozen=True)
class DummyOther:
    value: int


class TestAwaitEvent(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()

    def test_returns_event_published_from_another_thread(self) -> None:
        def producer() -> None:
            time.sleep(0.05)
            self.bus.publish(DummyDone(axis=2))

        threading.Thread(target=producer, daemon=True).start()
        event = await_event(self.bus, DummyDone, timeout=2.0)
        self.assertEqual(event.axis, 2)

    def test_match_filters_non_matching_events(self) -> None:
        def producer() -> None:
            time.sleep(0.03)
            self.bus.publish(DummyDone(axis=1))  # ignored by match
            time.sleep(0.03)
            self.bus.publish(DummyDone(axis=5))  # the one we want

        threading.Thread(target=producer, daemon=True).start()
        event = await_event(
            self.bus, DummyDone, match=lambda e: e.axis == 5, timeout=2.0
        )
        self.assertEqual(event.axis, 5)

    def test_emit_is_called_and_subscribe_happens_first(self) -> None:
        # emit publishes synchronously on THIS thread; because we subscribe before
        # emitting, even an instantaneous reply is captured (no missed-event race).
        calls = []

        def emit() -> None:
            calls.append(True)
            self.bus.publish(DummyDone(axis=9))

        event = await_event(self.bus, DummyDone, emit=emit, timeout=2.0)
        self.assertEqual(calls, [True])
        self.assertEqual(event.axis, 9)

    def test_timeout_raises(self) -> None:
        t0 = time.monotonic()
        with self.assertRaises(RoutineTimeout):
            await_event(self.bus, DummyDone, timeout=0.12)
        self.assertGreaterEqual(time.monotonic() - t0, 0.12)

    def test_cancel_raises_promptly(self) -> None:
        cancel = CancelToken()

        def canceller() -> None:
            time.sleep(0.05)
            cancel.cancel()

        threading.Thread(target=canceller, daemon=True).start()
        t0 = time.monotonic()
        with self.assertRaises(RoutineCancelled):
            await_event(self.bus, DummyDone, timeout=5.0, cancel=cancel)
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_unsubscribes_after_return(self) -> None:
        # After a completed wait, a later publish must not reach a leftover handler.
        self.bus.publish(DummyDone(axis=0))  # warm-up, no subs yet

        def producer() -> None:
            time.sleep(0.02)
            self.bus.publish(DummyDone(axis=3))

        threading.Thread(target=producer, daemon=True).start()
        await_event(self.bus, DummyDone, timeout=2.0)
        # The dict[type] entry should be gone (handler removed in finally).
        self.assertNotIn(DummyDone, self.bus._subs)


class TestAwaitReply(unittest.TestCase):
    def test_returns_reply_from_another_thread(self) -> None:
        def request(on_reply) -> None:
            def worker() -> None:
                time.sleep(0.05)
                on_reply("OK")

            threading.Thread(target=worker, daemon=True).start()

        reply = await_reply(request, timeout=2.0)
        self.assertEqual(reply, "OK")

    def test_timeout_when_no_reply(self) -> None:
        with self.assertRaises(RoutineTimeout):
            await_reply(lambda on_reply: None, timeout=0.1)


class TestCancellableSleep(unittest.TestCase):
    def test_sleeps_full_duration(self) -> None:
        t0 = time.monotonic()
        cancellable_sleep(0.1)
        self.assertGreaterEqual(time.monotonic() - t0, 0.1)

    def test_cancel_interrupts(self) -> None:
        cancel = CancelToken()

        def canceller() -> None:
            time.sleep(0.05)
            cancel.cancel()

        threading.Thread(target=canceller, daemon=True).start()
        t0 = time.monotonic()
        with self.assertRaises(RoutineCancelled):
            cancellable_sleep(5.0, cancel=cancel)
        self.assertLess(time.monotonic() - t0, 1.0)


class TestCancelToken(unittest.TestCase):
    def test_basic_flag_behavior(self) -> None:
        c = CancelToken()
        self.assertFalse(c.is_cancelled())
        c.raise_if_cancelled()  # no-op
        c.cancel()
        self.assertTrue(c.is_cancelled())
        with self.assertRaises(RoutineCancelled):
            c.raise_if_cancelled()
        c.reset()
        self.assertFalse(c.is_cancelled())


if __name__ == "__main__":
    unittest.main()
