"""Unit tests for the ESP serial driver (fake transport — no hardware)."""
import os
import sys
import unittest
from collections import deque

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Driver relocated into the Devices package (control_readout.esp_301) following the
# device pattern; tested here via the editable install (requires the 3.12 venv).
from control_readout.esp_301.esp_driver import EspDriver, EspError, Trajectory


class FakeSerial:
    """In-memory serial stand-in: captures writes, returns queued readline replies."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self.is_open = True
        self.writes: list[bytes] = []
        self._replies: deque[bytes] = deque(
            (r.encode("ascii") + b"\r\n") for r in (replies or [])
        )

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def readline(self) -> bytes:
        return self._replies.popleft() if self._replies else b""

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False

    # convenience
    @property
    def last_write(self) -> bytes:
        return self.writes[-1]


class TestMotionCommands(unittest.TestCase):
    def setUp(self) -> None:
        self.io = FakeSerial()
        self.drv = EspDriver(self.io)

    def test_move_to_formats_absolute_command(self):
        self.drv.move_to(1, -18.0)
        self.assertEqual(self.io.last_write, b"1PA-18.0000\r")

    def test_move_relative(self):
        self.drv.move_relative(2, 0.5)
        self.assertEqual(self.io.last_write, b"2PR0.5000\r")

    def test_stop_and_home(self):
        self.drv.stop(3)
        self.assertEqual(self.io.last_write, b"3ST\r")
        self.drv.home(1)
        self.assertEqual(self.io.last_write, b"1OR\r")
        self.drv.home(1, mode=2)
        self.assertEqual(self.io.last_write, b"1OR2\r")

    def test_set_velocity_and_motor(self):
        self.drv.set_velocity(1, 2.5)
        self.assertEqual(self.io.last_write, b"1VA2.5000\r")
        self.drv.motor_on(1)
        self.assertEqual(self.io.last_write, b"1MO\r")
        self.drv.motor_off(1)
        self.assertEqual(self.io.last_write, b"1MF\r")


class TestQueries(unittest.TestCase):
    def test_get_position_parses_float(self):
        io = FakeSerial(["-18.0000"])
        drv = EspDriver(io)
        self.assertAlmostEqual(drv.get_position(1), -18.0)
        self.assertEqual(io.last_write, b"1TP\r")

    def test_get_position_bad_reply_raises(self):
        drv = EspDriver(FakeSerial(["garbage"]))
        with self.assertRaises(EspError):
            drv.get_position(1)

    def test_get_error_zero(self):
        drv = EspDriver(FakeSerial(["0"]))
        self.assertEqual(drv.get_error(), 0)

    def test_raise_on_error(self):
        drv = EspDriver(FakeSerial(["7"]))
        with self.assertRaises(EspError) as ctx:
            drv.raise_on_error()
        self.assertEqual(ctx.exception.code, 7)

    def test_is_motion_done(self):
        self.assertTrue(EspDriver(FakeSerial(["1"])).is_motion_done(1))
        self.assertFalse(EspDriver(FakeSerial(["0"])).is_motion_done(1))


class TestAcquisition(unittest.TestCase):
    def test_setup_and_enable_framing(self):
        io = FakeSerial()
        drv = EspDriver(io)
        drv.setup_acquisition(2, rate_hz=100.0, n_samples=500, mode=1)
        self.assertEqual(io.last_write, b"DC1,2,0,0,100.0000,500\r")
        drv.enable_acquisition(True)
        self.assertEqual(io.last_write, b"DE1\r")
        drv.enable_acquisition(False)
        self.assertEqual(io.last_write, b"DE0\r")

    def test_read_acquisition_builds_trajectory(self):
        io = FakeSerial(["0.0, 1.0, 2.0, 3.0"])
        drv = EspDriver(io)
        traj = drv.read_acquisition(rate_hz=10.0)
        self.assertIsInstance(traj, Trajectory)
        self.assertEqual(traj.positions, [0.0, 1.0, 2.0, 3.0])
        self.assertEqual(len(traj), 4)
        for got, want in zip(traj.times_s, [0.0, 0.1, 0.2, 0.3]):
            self.assertAlmostEqual(got, want)

    def test_acquisition_count(self):
        self.assertEqual(EspDriver(FakeSerial(["250"])).acquisition_count(), 250)


class TestLifecycle(unittest.TestCase):
    def test_close(self):
        io = FakeSerial()
        EspDriver(io).close()
        self.assertFalse(io.is_open)


if __name__ == "__main__":
    unittest.main()
