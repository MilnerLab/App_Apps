"""Unit tests for the control_readout mock device drivers (RGV / picomotor / servo)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from base_core.math.enums import AngleUnit
from base_core.math.models import Angle
from control_readout.picomotor.config import PicomotorConfig
from control_readout.picomotor.mock_driver import MockPicomotor
from control_readout.rgv100bl.config import Rgv100Config
from control_readout.rgv100bl.mock_driver import MockRgvRotator
from control_readout.servo_shutter.config import ServoShutterConfig
from control_readout.servo_shutter.stub_driver import ManualShutterStub


class TestRgvMock(unittest.TestCase):
    def test_rotate_and_home(self):
        r = MockRgvRotator(Rgv100Config())
        self.assertAlmostEqual(float(r.current_angle), 0.0)
        r.rotate(Angle(45, AngleUnit.DEG))
        self.assertAlmostEqual(float(r.current_angle), float(Angle(45, AngleUnit.DEG)))
        r.home()
        self.assertAlmostEqual(float(r.current_angle), 0.0)


class TestPicomotorMock(unittest.TestCase):
    def test_step_accumulates_per_axis(self):
        p = MockPicomotor(PicomotorConfig())
        p.move_by(1, 50)
        p.move_by(1, -20)
        p.move_by(2, 10)
        self.assertEqual(p.position(1), 30)
        self.assertEqual(p.position(2), 10)
        self.assertEqual(p.position(3), 0)


class TestServoStub(unittest.TestCase):
    def test_block_unblock(self):
        s = ManualShutterStub(ServoShutterConfig())
        self.assertFalse(s.is_blocked(0))
        s.block(0)
        self.assertTrue(s.is_blocked(0))
        s.unblock(0)
        self.assertFalse(s.is_blocked(0))


if __name__ == "__main__":
    unittest.main()
