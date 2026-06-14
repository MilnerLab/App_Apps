"""Unit tests for run_pid_lock (M4) against a simulated plant — no hardware, no lab."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.control.lock import run_pid_lock
from app_apps.control.pid import PIDController


class _Plant:
    """A trivial first-order plant: the measured value IS the accumulated actuation."""

    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def measure(self) -> float:
        return self.value

    def actuate(self, step: float) -> None:
        self.value += step


class TestRunPidLock(unittest.TestCase):
    def test_converges_to_setpoint(self) -> None:
        plant = _Plant()
        pid = PIDController(kp=0.5, setpoint=10.0)
        result = run_pid_lock(
            pid=pid, measure=plant.measure, actuate=plant.actuate,
            tolerance=0.01, max_iterations=200,
        )
        self.assertTrue(result.converged)
        self.assertAlmostEqual(plant.value, 10.0, delta=0.01)

    def test_already_on_target_does_nothing(self) -> None:
        calls: list[float] = []
        pid = PIDController(kp=1.0, setpoint=5.0)
        result = run_pid_lock(
            pid=pid, measure=lambda: 5.0, actuate=calls.append,
            tolerance=0.1, max_iterations=10,
        )
        self.assertTrue(result.converged)
        self.assertEqual(result.iterations, 0)
        self.assertEqual(calls, [])  # never actuated

    def test_non_convergence_reported(self) -> None:
        plant = _Plant()
        pid = PIDController(kp=0.01, setpoint=100.0, output_limits=(-0.1, 0.1))
        result = run_pid_lock(
            pid=pid, measure=plant.measure, actuate=plant.actuate,
            tolerance=0.01, max_iterations=3,
        )
        self.assertFalse(result.converged)
        self.assertEqual(result.iterations, 3)

    def test_sleep_called_between_corrections(self) -> None:
        plant = _Plant()
        sleeps: list[float] = []
        pid = PIDController(kp=0.5, setpoint=10.0)
        run_pid_lock(
            pid=pid, measure=plant.measure, actuate=plant.actuate,
            tolerance=0.01, max_iterations=200, dt=0.2, sleep=sleeps.append,
        )
        self.assertGreater(len(sleeps), 0)
        self.assertTrue(all(s == 0.2 for s in sleeps))


if __name__ == "__main__":
    unittest.main()
