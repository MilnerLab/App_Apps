"""Unit tests for the `lab` facade (R.3).

Command devices are exercised with fake handles that emit the real telemetry events; the
scope/spectrometer consumer path is exercised against a real in-process shared-memory buffer
(acting as the producer) — no subprocess, no hardware.
"""
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.io.oscilloscope.buffer import ScopeBuffer, ScopeMemorySpec
from app_apps.io.oscilloscope.events import TraceAck, TraceAvailable
from app_apps.io.spectrometer.buffer import SpectrumBuffer, SpectrumMemorySpec
from app_apps.io.spectrometer.events import SpectrumAck, SpectrumAvailable
from app_apps.routines.linear.cancel import CancelToken
from app_apps.routines.linear.config import LabConfig
from app_apps.routines.linear.lab import Lab, LabUnavailable, SpectrumReading
from base_core.framework.events.event_bus import EventBus
from base_core.math.enums import AngleUnit
from base_core.math.models import Angle
from control_readout.esp_301.messages import MoveComplete, PositionUpdate
from control_readout.picomotor.messages import StepsMoved
from control_readout.rgv100bl.messages import HwpAngleUpdate
from control_readout.servo_shutter.messages import ArmStateChanged


# --- fake command handles: emit the real telemetry synchronously ---------------------


class FakeEsp:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self.pos: dict[int, float] = {}

    def move_to(self, axis: int, position: float) -> None:
        self.pos[axis] = position
        self._bus.publish(MoveComplete(axis=axis, position=position))

    def move_relative(self, axis: int, delta: float) -> None:
        self.pos[axis] = self.pos.get(axis, 0.0) + delta
        self._bus.publish(MoveComplete(axis=axis, position=self.pos[axis]))


class FakeRgv:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self.last = None

    def rotate_to(self, angle: Angle) -> None:
        self.last = angle
        self._bus.publish(HwpAngleUpdate(angle=angle))

    def home(self) -> None:
        self._bus.publish(HwpAngleUpdate(angle=Angle(0.0)))


class FakePico:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self.totals: dict[int, int] = {}

    def step(self, axis: int, steps: int) -> None:
        self.totals[axis] = self.totals.get(axis, 0) + steps
        self._bus.publish(StepsMoved(axis=axis, total_steps=self.totals[axis]))


class FakeServo:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self.state: dict[int, bool] = {}

    def block(self, arm: int) -> None:
        self.state[arm] = True
        self._bus.publish(ArmStateChanged(arm=arm, blocked=True))

    def unblock(self, arm: int) -> None:
        self.state[arm] = False
        self._bus.publish(ArmStateChanged(arm=arm, blocked=False))


class FakeWriterService:
    """Stands in for a WriterSubprocessService: just records consumer registration."""

    def __init__(self) -> None:
        self.registered: list[str] = []
        self.unregistered: list[str] = []

    def register_consumer(self, consumer_id: str) -> None:
        self.registered.append(consumer_id)

    def unregister_consumer(self, consumer_id: str) -> None:
        self.unregistered.append(consumer_id)


def _publish_later(bus: EventBus, event: object, delay: float = 0.03) -> None:
    threading.Thread(
        target=lambda: (time.sleep(delay), bus.publish(event)), daemon=True
    ).start()


class TestCommandVerbs(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.cancel = CancelToken()

    def _lab(self, **kw) -> Lab:
        return Lab(bus=self.bus, cancel=self.cancel, config=LabConfig(), **kw)

    def test_stage_move_to_returns_position(self) -> None:
        lab = self._lab(esp=FakeEsp(self.bus))
        try:
            pos = lab.delay.move_to(12.5)
            self.assertEqual(pos, 12.5)
            # subscribed PositionUpdate cache updates too
            self.bus.publish(PositionUpdate(axis=2, position=12.5))
            self.assertEqual(lab.delay.position, 12.5)
        finally:
            lab.close()

    def test_stage_move_by_accumulates(self) -> None:
        lab = self._lab(esp=FakeEsp(self.bus))
        try:
            lab.probe.move_by(1.0)
            self.assertEqual(lab.probe.move_by(0.5), 1.5)
        finally:
            lab.close()

    def test_hwp_rotate(self) -> None:
        rgv = FakeRgv(self.bus)
        lab = self._lab(rgv=rgv)
        lab.hwp.rotate_to(Angle(45, AngleUnit.DEG))
        self.assertIsNotNone(rgv.last)

    def test_picomotor_step_returns_total(self) -> None:
        lab = self._lab(picomotor=FakePico(self.bus))
        self.assertEqual(lab.picomotor.step(1, 50), 50)
        self.assertEqual(lab.picomotor.step(1, -20), 30)

    def test_shutter_open_close(self) -> None:
        servo = FakeServo(self.bus)
        lab = self._lab(servo=servo)
        lab.shutter.close(0)  # block
        self.assertTrue(servo.state[0])
        lab.shutter.open(0)  # unblock
        self.assertFalse(servo.state[0])

    def test_unavailable_device_raises(self) -> None:
        lab = self._lab()  # nothing wired
        with self.assertRaises(LabUnavailable):
            lab.hwp.rotate_to(Angle(0.0))
        with self.assertRaises(LabUnavailable):
            lab.qwp.rotate_to(Angle(0.0))
        with self.assertRaises(LabUnavailable):
            lab.probe.move_to(1.0)


class TestScopeCapture(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.spec = ScopeMemorySpec(
            f"test_scope_{uuid.uuid4().hex[:8]}", slot_count=2, channels=2, n_samples=8
        )
        self.writer = ScopeBuffer.create(self.spec)
        self.service = FakeWriterService()
        self.lab = Lab(
            bus=self.bus,
            cancel=CancelToken(),
            scope_service=self.service,
            scope_spec=self.spec,
            config=LabConfig(xcorr_top_n=3, capture_timeout_s=2.0),
            consumer_id="test",
        )

    def tearDown(self) -> None:
        self.lab.close()
        self.writer.unlink()
        self.writer.close()

    def test_capture_reads_channel_and_acks(self) -> None:
        ch0 = np.arange(8, dtype=np.float64)
        self.writer.write_trace(0, np.vstack([ch0, np.zeros(8)]))

        acks: list[TraceAck] = []
        self.bus.subscribe(TraceAck, acks.append)

        _publish_later(self.bus, TraceAvailable(slot=0, item_id=1, timestamp_ns=0))
        result = self.lab.scope.capture(channel=0)

        np.testing.assert_array_equal(result, ch0)
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0].slot, 0)
        self.assertEqual(self.service.registered, ["test"])
        self.assertEqual(self.service.unregistered, ["test"])

    def test_xcorr_point_is_mean_of_top_n(self) -> None:
        ch0 = np.array([1, 2, 3, 4, 5, 6, 7, 8], dtype=np.float64)
        self.writer.write_trace(0, np.vstack([ch0, np.zeros(8)]))
        _publish_later(self.bus, TraceAvailable(slot=0, item_id=2, timestamp_ns=0))
        # top 3 of [1..8] = {6,7,8}, mean 7.0
        self.assertEqual(self.lab.xcorr_point(), 7.0)


class TestSpectrometerRead(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.spec = SpectrumMemorySpec(
            f"test_spec_{uuid.uuid4().hex[:8]}", slot_count=2, pixel_count=8
        )
        self.writer = SpectrumBuffer.create(self.spec)
        self.service = FakeWriterService()
        self.lab = Lab(
            bus=self.bus,
            cancel=CancelToken(),
            spectrometer_service=self.service,
            spectrum_spec=self.spec,
            config=LabConfig(spectrum_timeout_s=2.0),
            consumer_id="test",
        )

    def tearDown(self) -> None:
        self.lab.close()
        self.writer.unlink()
        self.writer.close()

    def test_read_returns_wavelengths_and_intensities(self) -> None:
        wl = np.linspace(796.0, 810.0, 8)
        ins = np.arange(8, dtype=np.float64)
        self.writer.write_spectrum(0, wl, ins)

        acks: list[SpectrumAck] = []
        self.bus.subscribe(SpectrumAck, acks.append)

        _publish_later(self.bus, SpectrumAvailable(slot=0, item_id=1, timestamp_ns=0))
        reading = self.lab.spectrometer.read()

        self.assertIsInstance(reading, SpectrumReading)
        np.testing.assert_array_almost_equal(reading.wavelengths, wl)
        np.testing.assert_array_equal(reading.intensities, ins)
        self.assertEqual(len(acks), 1)


class TestDataHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Lab(bus=EventBus(), cancel=CancelToken())

    def test_frange_inclusive(self) -> None:
        self.assertEqual(list(self.lab.frange(0.0, 1.0, 0.5)), [0.0, 0.5, 1.0])

    def test_record_and_save_csv(self) -> None:
        self.lab.record(delay=1.0, nu0=380.0)
        self.lab.record(delay=2.0, nu0=381.0)
        with tempfile.TemporaryDirectory() as d:
            path = self.lab.save(os.path.join(d, "out.csv"))
            with open(path) as f:
                lines = [ln.strip() for ln in f if ln.strip()]
        self.assertEqual(lines[0], "delay,nu0")
        self.assertEqual(lines[1], "1.0,380.0")
        self.assertEqual(lines[2], "2.0,381.0")


if __name__ == "__main__":
    unittest.main()
