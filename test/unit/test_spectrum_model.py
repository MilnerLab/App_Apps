"""Structural unit tests for the spectrum forward model (numpy only)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.analysis.spectrum_info.model import (
    C_NM_THZ,
    SpectrumParams,
    bounded_chirp_intensity,
    envelope_edges_thz,
    gaussian_envelope,
    nu_thz,
)


def _params(**over):
    base = dict(
        central_wavelength_nm=804.0,
        bandwidth_nm=8.0,
        amp_upper=1.0,
        amp_lower=0.1,
        phase0=0.3,
        tau_ps=2.0,
        g2=0.0,
        g3=0.0,
    )
    base.update(over)
    return SpectrumParams(**base)


def _count_crossings(y: np.ndarray) -> int:
    s = np.sign(y - np.mean(y))
    return int(np.count_nonzero(np.diff(s) != 0))


class TestModel(unittest.TestCase):
    def setUp(self):
        self.w = np.linspace(795.0, 815.0, 2048)

    def test_nu_conversion(self):
        self.assertAlmostEqual(nu_thz(800.0), C_NM_THZ / 800.0)

    def test_bounded_between_envelopes(self):
        p = _params()
        g = gaussian_envelope(self.w, p.central_wavelength_nm, p.bandwidth_nm)
        e_up, e_lo = p.amp_upper * g, p.amp_lower * g
        I = bounded_chirp_intensity(self.w, p)
        eps = 1e-9
        self.assertTrue(np.all(I <= e_up + eps))
        self.assertTrue(np.all(I >= e_lo - eps))

    def test_equal_envelopes_have_no_fringe(self):
        p = _params(amp_lower=1.0, amp_upper=1.0)
        g = gaussian_envelope(self.w, p.central_wavelength_nm, p.bandwidth_nm)
        I = bounded_chirp_intensity(self.w, p)
        self.assertTrue(np.allclose(I, 1.0 * g))

    def test_zero_lower_envelope_minima_reach_zero(self):
        p = _params(amp_lower=0.0, tau_ps=3.0)
        I = bounded_chirp_intensity(self.w, p)
        # somewhere near the peak a fringe minimum touches ~0
        self.assertLess(float(I.min()), 1e-6)

    def test_more_chirp_more_oscillations(self):
        slow = _count_crossings(bounded_chirp_intensity(self.w, _params(tau_ps=1.0)))
        fast = _count_crossings(bounded_chirp_intensity(self.w, _params(tau_ps=5.0)))
        self.assertGreater(fast, slow)

    def test_envelope_edges_ordered(self):
        nu0, nu_blue, nu_red = envelope_edges_thz(_params())
        # blue edge (shorter λ) is higher frequency than red edge
        self.assertGreater(nu_blue, nu0)
        self.assertGreater(nu0, nu_red)


if __name__ == "__main__":
    unittest.main()
