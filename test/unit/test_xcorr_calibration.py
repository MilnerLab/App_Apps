"""Unit tests for the XCORR cross-correlation + wavelength↔delay calibration."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.analysis.xcorr.calibration import (
    WavelengthDelayCalibration,
    cross_correlate,
    lag_to_delay_ps,
)


def _bump(n: int, center: int, width: int = 20) -> np.ndarray:
    x = np.zeros(n)
    x[center : center + width] = np.hanning(width)
    return x


class TestCrossCorrelate(unittest.TestCase):
    def test_zero_lag_for_identical(self):
        a = _bump(200, 90)
        self.assertEqual(cross_correlate(a, a), 0)

    def test_recovers_shift_magnitude_and_antisymmetry(self):
        a = _bump(200, 80)
        s = 7
        b = _bump(200, 80 + s)  # b delayed (later) by s samples
        lag_ab = cross_correlate(a, b)
        lag_ba = cross_correlate(b, a)
        self.assertEqual(abs(lag_ab), s)
        self.assertEqual(lag_ab, -lag_ba)

    def test_lag_to_delay(self):
        self.assertAlmostEqual(lag_to_delay_ps(10, 0.05), 0.5)


class TestCalibration(unittest.TestCase):
    def setUp(self):
        self.cal = WavelengthDelayCalibration(
            created_utc="2026-06-12T10:00:00+00:00",
            grating_stage="esp100",
            grating_position=12.0,
            delay_stage="esp301",
            delay_position=3.0,
            wavelengths_nm=np.array([810.0, 800.0, 805.0]),  # intentionally unsorted
            delays_ps=np.array([2.0, 0.0, 1.0]),
        )

    def test_sorted_by_wavelength(self):
        self.assertTrue(np.all(np.diff(self.cal.wavelengths_nm) > 0))
        # delays follow the wavelength sort: 800->0, 805->1, 810->2
        np.testing.assert_allclose(self.cal.delays_ps, [0.0, 1.0, 2.0])

    def test_wavelength_to_delay_interpolates(self):
        self.assertAlmostEqual(float(self.cal.wavelength_to_delay(802.5)), 0.5)

    def test_delay_to_wavelength_interpolates(self):
        self.assertAlmostEqual(float(self.cal.delay_to_wavelength(1.5)), 807.5)

    def test_combination_key(self):
        self.assertEqual(self.cal.combination, "esp100@12__esp301@3")

    def test_validation(self):
        with self.assertRaises(ValueError):
            WavelengthDelayCalibration(
                created_utc="t", grating_stage="g", grating_position=0.0,
                delay_stage="d", delay_position=0.0,
                wavelengths_nm=np.array([800.0]), delays_ps=np.array([0.0]),
            )
        with self.assertRaises(ValueError):
            WavelengthDelayCalibration(
                created_utc="t", grating_stage="g", grating_position=0.0,
                delay_stage="d", delay_position=0.0,
                wavelengths_nm=np.array([800.0, 801.0]), delays_ps=np.array([0.0]),
            )


if __name__ == "__main__":
    unittest.main()
