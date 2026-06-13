"""Unit tests for LinearRoutineRunner (R.4).

Uses a real TaskRunner (1-worker pool) and a real device-less Lab, so the full async
lifecycle is exercised: launch -> background execution -> lifecycle events, plus single-
flight rejection, cooperative cancellation, and failure handling.
"""
import os
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.routines.linear.cancel import CancelToken
from app_apps.routines.linear.events import (
    RoutineCancelledEvent,
    RoutineCompleted,
    RoutineFailed,
    RoutineStarted,
)
from app_apps.routines.linear.lab import Lab
from app_apps.routines.linear.registry import RoutineNotFound, clear_registry, routine
from app_apps.routines.linear.runner import LinearRoutineRunner, RoutineBusy, RoutineError
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus


def _wait(predicate, timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestRunner(unittest.TestCase):
    def setUp(self) -> None:
        clear_registry()
        self.bus = EventBus()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.io = TaskRunner(self.executor)
        self.runner = LinearRoutineRunner(
            self.bus,
            self.io,
            lab_factory=lambda cancel, params: Lab(bus=self.bus, cancel=cancel, params=params),
        )
        self.started: list[RoutineStarted] = []
        self.completed: list[RoutineCompleted] = []
        self.failed: list[RoutineFailed] = []
        self.cancelled: list[RoutineCancelledEvent] = []
        self.bus.subscribe(RoutineStarted, self.started.append)
        self.bus.subscribe(RoutineCompleted, self.completed.append)
        self.bus.subscribe(RoutineFailed, self.failed.append)
        self.bus.subscribe(RoutineCancelledEvent, self.cancelled.append)

    def tearDown(self) -> None:
        self.runner.stop()
        self.executor.shutdown(wait=True)
        clear_registry()

    def test_initially_idle(self) -> None:
        self.assertFalse(self.runner.is_running)
        self.assertIsNone(self.runner.active_routine)

    def test_launch_runs_and_completes(self) -> None:
        marker = []

        @routine("simple")
        def simple(lab):
            marker.append("ran")
            lab.record(ok=1)

        self.runner.launch("simple")
        self.assertTrue(_wait(lambda: self.completed))
        self.assertEqual(marker, ["ran"])
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.completed[0].name, "simple")
        self.assertFalse(self.runner.is_running)

    def test_params_are_passed_through(self) -> None:
        seen = {}

        @routine("with_params")
        def with_params(lab, a, b=10):
            seen["a"] = a
            seen["b"] = b

        self.runner.launch("with_params", a=3)
        self.assertTrue(_wait(lambda: self.completed))
        self.assertEqual(seen, {"a": 3, "b": 10})
        self.assertEqual(self.started[0].params, {"a": 3})

    def test_single_flight_rejects_second_launch(self) -> None:
        release = threading.Event()
        running = threading.Event()

        @routine("blocker")
        def blocker(lab):
            running.set()
            release.wait(5.0)

        self.runner.launch("blocker")
        self.assertTrue(running.wait(2.0))
        self.assertTrue(self.runner.is_running)
        with self.assertRaises(RoutineBusy):
            self.runner.launch("blocker")
        release.set()
        self.assertTrue(_wait(lambda: self.completed))

    def test_stop_cancels_running_routine(self) -> None:
        running = threading.Event()

        @routine("looper")
        def looper(lab):
            running.set()
            while True:
                lab.sleep(0.02)  # cancellable

        self.runner.launch("looper")
        self.assertTrue(running.wait(2.0))
        self.runner.stop()
        self.assertTrue(_wait(lambda: self.cancelled))
        self.assertEqual(self.cancelled[0].name, "looper")
        self.assertFalse(self.runner.is_running)
        self.assertEqual(self.completed, [])

    def test_failure_publishes_failed_event(self) -> None:
        @routine("boom")
        def boom(lab):
            raise ValueError("nope")

        self.runner.launch("boom")
        self.assertTrue(_wait(lambda: self.failed))
        self.assertIn("ValueError", self.failed[0].error)
        self.assertFalse(self.runner.is_running)
        self.assertEqual(self.completed, [])

    def test_unknown_routine_raises(self) -> None:
        with self.assertRaises(RoutineNotFound):
            self.runner.launch("does_not_exist")
        # claiming nothing on failure
        self.assertFalse(self.runner.is_running)

    def test_start_is_not_supported(self) -> None:
        with self.assertRaises(RoutineError):
            self.runner.start()

    def test_relaunch_after_completion(self) -> None:
        @routine("once")
        def once(lab):
            lab.record(x=1)

        self.runner.launch("once")
        self.assertTrue(_wait(lambda: len(self.completed) == 1))
        self.runner.launch("once")
        self.assertTrue(_wait(lambda: len(self.completed) == 2))


if __name__ == "__main__":
    unittest.main()
