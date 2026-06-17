"""Rigorous edge-case tests for the `lab` facade (R.3).

Covers failure/cancellation/cleanup paths and subtle correctness properties that the happy-
path tests in test_routine_lab.py don't: timeouts, mid-verb cancellation, the register/ack/
unregister `finally` guarantees (incl. when the read raises), match-on-axis correctness,
copy independence of captured data, and facade lifecycle.
"""
import os
import sys
import threading
import time
import unittest
import uuid

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.io.oscilloscope.buffer import ScopeBuffer, ScopeMemorySpec
from app_apps.io.oscilloscope.events import TraceAck, TraceAvailable
from app_apps.routines.linear.cancel import CancelToken, RoutineCancelled, RoutineTimeout
from app_apps.routines.linear.config import LabConfig
from app_apps.routines.linear.lab import Lab
from base_core.framework.events.event_bus import EventBus
from control_readout.esp_301.messages import MoveComplete, PositionUpdate


def _publish_later(bus: EventBus, event: object, delay: float = 0.03) -> None:
    threading.Thread(
        target=lambda: (time.sleep(delay), bus.publish(event)), daemon=True
    ).start()


class SilentEsp:
    """Accepts move commands but never signals completion (to force timeout/cancel)."""

    def move_to(self, axis: int, position: float) -> None:
        pass

    def move_relative(self, axis: int, delta: float) -> None:
        pass


class DecoyEsp:
    """Publishes a wrong-axis completion before the correct one, both synchronously."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def move_to(self, axis: int, position: float) -> None:
        self._bus.publish(MoveComplete(axis=axis + 100, position=-999.0))  # decoy
        self._bus.publish(MoveComplete(axis=axis, position=position))  # real

    def move_relative(self, axis: int, delta: float) -> None:
        self._bus.publish(MoveComplete(axis=axis, position=delta))


class InstantEsp:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def move_to(self, axis: int, position: float) -> None:
        self._bus.publish(MoveComplete(axis=axis, position=position))

    def move_relative(self, axis: int, delta: float) -> None:
        self._bus.publish(MoveComplete(axis=axis, position=delta))


class FakeWriterService:
    def __init__(self) -> None:
        self.registered: list[str] = []
        self.unregistered: list[str] = []

    def register_consumer(self, consumer_id: str) -> None:
        self.registered.append(consumer_id)

    def unregister_consumer(self, consumer_id: str) -> None:
        self.unregistered.append(consumer_id)


class BoomBuffer:
    def read_trace(self, slot: int) -> np.ndarray:
        raise RuntimeError("boom")

    def close(self) -> None:  # realistic buffer fakes are closeable
        pass


# ----------------------------------------------------------------------------------------
# Command-verb failure / cancellation / match correctness
# ----------------------------------------------------------------------------------------


class TestCommandFailureModes(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.cancel = CancelToken()

    def test_move_times_out_without_completion(self) -> None:
        lab = Lab(
            bus=self.bus, cancel=self.cancel, esp=SilentEsp(),
            config=LabConfig(move_timeout_s=0.1, poll_s=0.02),
        )
        try:
            t0 = time.monotonic()
            with self.assertRaises(RoutineTimeout):
                lab.probe.move_to(1.0)
            self.assertGreaterEqual(time.monotonic() - t0, 0.1)
        finally:
            lab.close()

    def test_move_cancelled_midway(self) -> None:
        lab = Lab(
            bus=self.bus, cancel=self.cancel, esp=SilentEsp(),
            config=LabConfig(move_timeout_s=5.0, poll_s=0.02),
        )
        try:
            threading.Thread(
                target=lambda: (time.sleep(0.05), self.cancel.cancel()), daemon=True
            ).start()
            t0 = time.monotonic()
            with self.assertRaises(RoutineCancelled):
                lab.probe.move_to(1.0)
            self.assertLess(time.monotonic() - t0, 1.0)
        finally:
            lab.close()

    def test_move_matches_only_its_own_axis(self) -> None:
        lab = Lab(bus=self.bus, cancel=self.cancel, esp=DecoyEsp(self.bus))
        try:
            # truncation is axis 3; a decoy axis-103 completion must be ignored.
            pos = lab.truncation.move_to(7.5)
            self.assertEqual(pos, 7.5)
        finally:
            lab.close()

    def test_settle_delay_is_applied(self) -> None:
        lab = Lab(
            bus=self.bus, cancel=self.cancel, esp=InstantEsp(self.bus),
            config=LabConfig(settle_s=0.1, poll_s=0.02),
        )
        try:
            t0 = time.monotonic()
            lab.delay.move_to(1.0)  # completes instantly; only settle should elapse
            self.assertGreaterEqual(time.monotonic() - t0, 0.09)
        finally:
            lab.close()


# ----------------------------------------------------------------------------------------
# Scope consumer: cleanup guarantees + data integrity
# ----------------------------------------------------------------------------------------


class TestScopeConsumerRigor(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.spec = ScopeMemorySpec(
            f"rig_scope_{uuid.uuid4().hex[:8]}", slot_count=2, channels=2, n_samples=8
        )
        self.writer = ScopeBuffer.create(self.spec)
        self.service = FakeWriterService()
        self.lab = Lab(
            bus=self.bus, cancel=CancelToken(),
            scope_handle=self.service, scope_spec=self.spec,
            config=LabConfig(capture_timeout_s=2.0), consumer_id="rig",
        )

    def tearDown(self) -> None:
        self.lab.close()
        self.writer.unlink()
        self.writer.close()

    def test_unregister_runs_even_when_read_raises(self) -> None:
        self.lab.scope._buffer = BoomBuffer()  # force the read to blow up
        _publish_later(self.bus, TraceAvailable(slot=0, item_id=1, timestamp_ns=0))
        with self.assertRaises(RuntimeError):
            self.lab.scope.capture()
        # the finally must still have unregistered the consumer (no leaked slot)
        self.assertEqual(self.service.registered, ["rig"])
        self.assertEqual(self.service.unregistered, ["rig"])

    def test_capture_timeout_still_unregisters(self) -> None:
        self.lab.scope._config = LabConfig(capture_timeout_s=0.1, poll_s=0.02)
        with self.assertRaises(RoutineTimeout):
            self.lab.scope.capture()  # no TraceAvailable ever published
        self.assertEqual(self.service.unregistered, ["rig"])

    def test_register_unregister_once_per_capture(self) -> None:
        ch = np.arange(8, dtype=np.float64)
        self.writer.write_trace(0, np.vstack([ch, np.zeros(8)]))
        for item in (1, 2):
            _publish_later(self.bus, TraceAvailable(slot=0, item_id=item, timestamp_ns=0))
            self.lab.scope.capture()
        self.assertEqual(self.service.registered, ["rig", "rig"])
        self.assertEqual(self.service.unregistered, ["rig", "rig"])

    def test_capture_returns_independent_copy(self) -> None:
        ch = np.arange(8, dtype=np.float64)
        self.writer.write_trace(0, np.vstack([ch, np.zeros(8)]))
        _publish_later(self.bus, TraceAvailable(slot=0, item_id=1, timestamp_ns=0))
        result = self.lab.scope.capture(channel=0)

        result[0] = -123.0  # mutate the returned array
        # the shared-memory slot must be untouched by that mutation
        np.testing.assert_array_equal(self.writer.read_trace(0)[0], ch)

    def test_capture_channel_none_returns_all_channels(self) -> None:
        self.writer.write_trace(0, np.vstack([np.ones(8), np.full(8, 2.0)]))
        _publish_later(self.bus, TraceAvailable(slot=0, item_id=1, timestamp_ns=0))
        result = self.lab.scope.capture(channel=None)
        self.assertEqual(result.shape, (2, 8))

    def test_xcorr_top_n_larger_than_trace(self) -> None:
        ch = np.array([1, 2, 3, 4, 0, 0, 0, 0], dtype=np.float64)
        self.writer.write_trace(0, np.vstack([ch, np.zeros(8)]))
        _publish_later(self.bus, TraceAvailable(slot=0, item_id=1, timestamp_ns=0))
        # top_n=20 > 8 samples -> mean of all 8 = (1+2+3+4)/8 = 1.25
        val = self.lab.xcorr_point(n_top=20)
        self.assertAlmostEqual(val, 1.25)


# ----------------------------------------------------------------------------------------
# Lifecycle + helpers
# ----------------------------------------------------------------------------------------


class TestLifecycleAndHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()

    def test_close_unsubscribes_position_updates(self) -> None:
        lab = Lab(bus=self.bus, cancel=CancelToken(), esp=InstantEsp(self.bus))
        lab.delay.move_to(5.0)
        self.assertEqual(lab.delay.position, 5.0)
        lab.close()
        # after close, a stray PositionUpdate must not mutate the cached position
        self.bus.publish(PositionUpdate(axis=2, position=99.0))
        self.assertEqual(lab.delay.position, 5.0)

    def test_close_is_idempotent(self) -> None:
        lab = Lab(bus=self.bus, cancel=CancelToken(), esp=InstantEsp(self.bus))
        lab.close()
        lab.close()  # must not raise

    def test_sleep_is_cancellable(self) -> None:
        cancel = CancelToken()
        lab = Lab(bus=self.bus, cancel=cancel)
        threading.Thread(
            target=lambda: (time.sleep(0.05), cancel.cancel()), daemon=True
        ).start()
        with self.assertRaises(RoutineCancelled):
            lab.sleep(5.0)

    def test_frange_zero_step_raises(self) -> None:
        lab = Lab(bus=self.bus, cancel=CancelToken())
        with self.assertRaises(ValueError):
            list(lab.frange(0.0, 1.0, 0.0))

    def test_frange_negative_step(self) -> None:
        lab = Lab(bus=self.bus, cancel=CancelToken())
        self.assertEqual(list(lab.frange(1.0, 0.0, -0.5)), [1.0, 0.5, 0.0])

    def test_frange_checkpoint_cancels(self) -> None:
        cancel = CancelToken()
        lab = Lab(bus=self.bus, cancel=cancel)
        cancel.cancel()
        with self.assertRaises(RoutineCancelled):
            list(lab.frange(0.0, 10.0, 1.0))

    def test_save_unions_heterogeneous_columns(self) -> None:
        import tempfile

        lab = Lab(bus=self.bus, cancel=CancelToken())
        lab.record(a=1)
        lab.record(b=2)  # different key set
        with tempfile.TemporaryDirectory() as d:
            path = lab.save(os.path.join(d, "out.csv"))
            with open(path) as f:
                header = f.readline().strip()
        self.assertEqual(header, "a,b")


if __name__ == "__main__":
    unittest.main()
