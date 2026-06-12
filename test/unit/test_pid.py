"""Unit tests for the PID controller."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.control.pid import PIDController


class TestProportional(unittest.TestCase):
    def test_pure_proportional(self):
        pid = PIDController(kp=2.0, setpoint=10.0)
        self.assertAlmostEqual(pid.update(8.0, dt=1.0), 4.0)  # error 2 * kp 2

    def test_output_clamped(self):
        pid = PIDController(kp=100.0, setpoint=10.0, output_limits=(-1.0, 1.0))
        self.assertEqual(pid.update(0.0, dt=1.0), 1.0)
        pid.setpoint = -10.0
        pid.reset()
        self.assertEqual(pid.update(0.0, dt=1.0), -1.0)

    def test_zero_dt_holds_output(self):
        pid = PIDController(kp=1.0, setpoint=5.0)
        first = pid.update(0.0, dt=1.0)
        self.assertEqual(pid.update(0.0, dt=0.0), first)


class TestDeadband(unittest.TestCase):
    def test_deadband_holds_output(self):
        pid = PIDController(kp=1.0, setpoint=10.0, deadband=0.5)
        # error 0.3 < deadband 0.5 -> no action, holds last output (0)
        self.assertEqual(pid.update(9.7, dt=1.0), 0.0)

    def test_outside_deadband_acts(self):
        pid = PIDController(kp=1.0, setpoint=10.0, deadband=0.5)
        self.assertAlmostEqual(pid.update(8.0, dt=1.0), 2.0)


class TestSlewLimit(unittest.TestCase):
    def test_slew_limits_change_per_second(self):
        pid = PIDController(kp=100.0, setpoint=10.0, slew_limit=1.0)
        # first call: no previous output, slew not yet applied
        out1 = pid.update(9.0, dt=1.0)
        # large demand but limited to +1.0/s from previous
        out2 = pid.update(0.0, dt=1.0)
        self.assertLessEqual(out2 - out1, 1.0 + 1e-9)


class TestIntegral(unittest.TestCase):
    def test_integral_drives_to_setpoint(self):
        # Simple integrating plant: x_{k+1} = x_k + u*dt
        pid = PIDController(kp=0.5, ki=0.5, setpoint=1.0, output_limits=(-5, 5))
        x = 0.0
        dt = 0.1
        for _ in range(2000):
            u = pid.update(x, dt)
            x += u * dt
        self.assertAlmostEqual(x, 1.0, places=2)

    def test_anti_windup_clamps_integral(self):
        pid = PIDController(
            kp=0.0, ki=1.0, setpoint=10.0, output_limits=(-1.0, 1.0)
        )
        # Saturated error for a long time should not wind the integral unbounded.
        for _ in range(1000):
            out = pid.update(0.0, dt=1.0)
        self.assertLessEqual(out, 1.0)
        # Because the integral was clamped (not wound up huge), a *reversed* error
        # should pull the output off the top rail within a couple of steps.
        pid.setpoint = 0.0
        recovered = False
        for _ in range(2):
            if pid.update(5.0, dt=1.0) < 1.0:  # measurement 5 > setpoint 0 -> error -5
                recovered = True
                break
        self.assertTrue(recovered)


if __name__ == "__main__":
    unittest.main()
