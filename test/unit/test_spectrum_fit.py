"""Round-trip fit test: generate synthetic spectrum with known params, recover them."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.analysis.spectrum_info.generator import synthetic_spectrum, wavelength_grid
from app_apps.analysis.spectrum_info.model import SpectrumParams

try:
    import lmfit  # noqa: F401
    from app_apps.analysis.spectrum_info.fit import fit_spectrum
    _HAS_LMFIT = True
except Exception:
    _HAS_LMFIT = False


TRUTH = SpectrumParams(
    central_wavelength_nm=804.0,
    bandwidth_nm=8.0,
    amp_upper=1.0,
    amp_lower=0.1,
    phase0=0.4,
    tau_ps=0.8,   # gentle chirp (~3 fringes) -> wider convergence basin for a unit test
    g2=0.0,
    g3=0.0,
)


@unittest.skipUnless(_HAS_LMFIT, "lmfit not installed")
class TestFit(unittest.TestCase):
    def setUp(self):
        self.w = wavelength_grid(795.0, 813.0, 1500)
        self.y = synthetic_spectrum(self.w, TRUTH, noise=0.004, seed=1)

    def test_recovers_known_parameters(self):
        # Seed near truth (a few % off) — unit test of the model+fit machinery,
        # not global convergence from scratch.
        init = SpectrumParams(
            central_wavelength_nm=804.15,
            bandwidth_nm=8.3,
            amp_upper=0.95,
            amp_lower=0.13,
            phase0=0.32,
            tau_ps=0.85,
            g2=0.0,
            g3=0.0,
        )
        info = fit_spectrum(self.w, self.y, init=init)

        self.assertAlmostEqual(info.central_wavelength_nm, TRUTH.central_wavelength_nm, delta=0.3)
        self.assertAlmostEqual(info.bandwidth_nm, TRUTH.bandwidth_nm, delta=0.5)
        self.assertAlmostEqual(info.amp_upper, TRUTH.amp_upper, delta=0.06)
        self.assertAlmostEqual(info.amp_lower, TRUTH.amp_lower, delta=0.05)
        self.assertAlmostEqual(info.tau_ps, TRUTH.tau_ps, delta=0.1)
        self.assertLess(info.fit_residual, 0.05)
        # frequency bookkeeping populated
        self.assertGreater(info.nu_start_thz, info.nu_end_thz)

    def test_lower_envelope_metric_exposed(self):
        info = fit_spectrum(self.w, self.y, init=TRUTH)
        self.assertEqual(info.lower_envelope_metric, info.amp_lower)


if __name__ == "__main__":
    unittest.main()
