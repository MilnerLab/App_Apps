"""Integration test for the lock_central_frequency routine (M4) against a simulated plant.

A fake lab models ν₀ = base + slope·(delay position): the routine measures ν₀, the PID nudges
the delay stage, and ν₀ converges to the target — all in software, no hardware.
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.routines.linear.scripts.control_loops import (
    lock_central_frequency,
    lock_phase,
    lock_terminal_frequency,
)


class FakeLab:
    """Simulated central-frequency plant exposing the lab verbs the routine uses."""

    def __init__(self, base: float, slope: float) -> None:
        self._pos = 0.0
        self._base = base
        self._slope = slope
        self.records: list[dict] = []
        lab = self

        class _Delay:
            def move_by(self, step_mm: float) -> float:
                lab._pos += step_mm
                return lab._pos

        class _Spec:
            def read(self):
                return None  # the fake fits from plant state, not this payload

        self.delay = _Delay()
        self.spectrometer = _Spec()

    def _nu0(self) -> float:
        return self._base + self._slope * self._pos

    def fit_spectrum(self, _reading) -> SimpleNamespace:
        return SimpleNamespace(nu0_thz=self._nu0())

    def sleep(self, _seconds: float) -> None:
        pass

    def log(self, _msg: str) -> None:
        pass

    def record(self, **fields) -> None:
        self.records.append(fields)


class FakeTruncationLab:
    """Simulated terminal-frequency plant: ν_end = base + slope·(truncation position)."""

    def __init__(self, base: float, slope: float) -> None:
        self._pos = 0.0
        self._base = base
        self._slope = slope
        self.records: list[dict] = []
        lab = self

        class _Trunc:
            def move_by(self, step_mm: float) -> float:
                lab._pos += step_mm
                return lab._pos

        class _Spec:
            def read(self):
                return None

        self.truncation = _Trunc()
        self.spectrometer = _Spec()

    def _nu_end(self) -> float:
        return self._base + self._slope * self._pos

    def fit_spectrum(self, _reading) -> SimpleNamespace:
        return SimpleNamespace(nu_end_thz=self._nu_end())

    def sleep(self, _seconds: float) -> None:
        pass

    def log(self, _msg: str) -> None:
        pass

    def record(self, **fields) -> None:
        self.records.append(fields)


class FakePhaseLab:
    """Simulated phase plant: φ0 = base + slope·(commanded HWP angle, rad)."""

    def __init__(self, base: float, slope: float) -> None:
        self._angle = 0.0
        self._base = base
        self._slope = slope
        self.records: list[dict] = []
        lab = self

        class _Hwp:
            def rotate_to(self, angle) -> None:
                lab._angle = float(angle)  # Angle is a float subclass (radians)

        class _Spec:
            def read(self):
                return None

        self.hwp = _Hwp()
        self.spectrometer = _Spec()

    def _phase0(self) -> float:
        return self._base + self._slope * self._angle

    def fit_spectrum(self, _reading) -> SimpleNamespace:
        return SimpleNamespace(phase0=self._phase0())

    def sleep(self, _seconds: float) -> None:
        pass

    def log(self, _msg: str) -> None:
        pass

    def record(self, **fields) -> None:
        self.records.append(fields)


class TestLockCentralFrequency(unittest.TestCase):
    def test_converges_to_target(self) -> None:
        lab = FakeLab(base=370.0, slope=2.0)  # ν0 = 370 + 2·pos; target 380 -> pos 5
        result = lock_central_frequency(
            lab, target_thz=380.0, kp=0.3, tolerance_thz=0.05, max_iterations=300, dt_s=0.1
        )
        self.assertTrue(result.converged)
        self.assertAlmostEqual(lab._nu0(), 380.0, delta=0.05)
        self.assertTrue(lab.records[-1]["converged"])
        self.assertEqual(lab.records[-1]["target_thz"], 380.0)

    def test_reports_non_convergence_when_capped(self) -> None:
        lab = FakeLab(base=0.0, slope=1.0)
        # target far away, tiny step cap, few iterations -> cannot reach
        result = lock_central_frequency(
            lab, target_thz=1000.0, kp=0.1, tolerance_thz=0.01,
            max_iterations=5, dt_s=0.1, max_step_mm=0.1,
        )
        self.assertFalse(result.converged)


class TestLockTerminalFrequency(unittest.TestCase):
    def test_converges_to_target(self) -> None:
        lab = FakeTruncationLab(base=180.0, slope=2.0)  # ν_end = 180 + 2·pos; target 200 -> pos 10
        result = lock_terminal_frequency(
            lab, target_thz=200.0, kp=0.3, tolerance_thz=0.05, max_iterations=300, dt_s=0.1
        )
        self.assertTrue(result.converged)
        self.assertAlmostEqual(lab._nu_end(), 200.0, delta=0.05)
        self.assertTrue(lab.records[-1]["converged"])
        self.assertEqual(lab.records[-1]["target_thz"], 200.0)


class TestLockPhase(unittest.TestCase):
    def test_converges_to_target(self) -> None:
        lab = FakePhaseLab(base=0.0, slope=1.0)  # φ0 = angle; target 0.5 rad -> angle 0.5
        result = lock_phase(
            lab, target_rad=0.5, kp=0.3, tolerance_rad=0.01, max_iterations=300, dt_s=0.1
        )
        self.assertTrue(result.converged)
        self.assertAlmostEqual(lab._phase0(), 0.5, delta=0.01)
        self.assertTrue(lab.records[-1]["converged"])

    def test_tracks_commanded_angle_from_start(self) -> None:
        # start_angle offsets the tracked angle; with slope 1 and base 0, φ0 == angle.
        lab = FakePhaseLab(base=0.0, slope=1.0)
        result = lock_phase(
            lab, target_rad=0.3, start_angle_rad=0.1, kp=0.3,
            tolerance_rad=0.01, max_iterations=300, dt_s=0.1,
        )
        self.assertTrue(result.converged)
        self.assertAlmostEqual(lab.records[-1]["final_angle_rad"], 0.3, delta=0.01)


if __name__ == "__main__":
    unittest.main()
