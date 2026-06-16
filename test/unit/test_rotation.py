"""Unit tests for the spectrum->rotational-frequency conversion (rotation.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.analysis.spectrum_info.model import SpectrumInfo
from app_apps.analysis.spectrum_info.rotation import (
    PulseChirp,
    rotational_frequency_ghz,
    start_rotational_frequency_ghz,
    terminal_rotational_frequency_ghz,
)


def _info(*, tau_ps: float, g2: float = 0.0, g3: float = 0.0,
          nu0=373.80, nu_start=377.0, nu_end=370.7) -> SpectrumInfo:
    return SpectrumInfo(
        central_wavelength_nm=802.0, bandwidth_nm=8.0, amp_upper=1.0, amp_lower=0.05,
        phase0=0.0, tau_ps=tau_ps, g2=g2, g3=g3,
        nu0_thz=nu0, nu_start_thz=nu_start, nu_end_thz=nu_end, fit_residual=0.0,
    )


class TestPulseChirp(unittest.TestCase):
    def test_chirp_rate_from_pulse_params(self) -> None:
        chirp = PulseChirp()  # 300 ps, 802 nm, 8 nm
        self.assertAlmostEqual(chirp.chirp_rate_thz_per_ps, 0.012429, places=5)


class TestRotationalFrequency(unittest.TestCase):
    def test_constant_rotation_when_no_chirp(self) -> None:
        # g2=g3=0 -> fringe rate is tau at every nu -> rotation = 0.5 * tau * R * 1000, flat.
        chirp = PulseChirp()
        info = _info(tau_ps=16.091)  # tuned so rotation ~= 100 GHz
        rot0 = rotational_frequency_ghz(info, info.nu0_thz, chirp)
        rot_end = terminal_rotational_frequency_ghz(info, chirp)
        self.assertAlmostEqual(rot0, 100.0, delta=0.5)
        self.assertAlmostEqual(rot_end, 100.0, delta=0.5)  # flat without chirp

    def test_recovers_10_to_200_ghz_sweep(self) -> None:
        # tau/g2 chosen (offline) so the rotation sweeps 10 GHz at the blue edge -> 200 GHz at the
        # red (truncation) edge, given the default pulse chirp. Validates factor 1/2 + g2 term +
        # edge selection together.
        chirp = PulseChirp()
        info = _info(tau_ps=17.139, g2=-2.4266)
        self.assertAlmostEqual(start_rotational_frequency_ghz(info, chirp), 10.0, delta=1.0)
        self.assertAlmostEqual(terminal_rotational_frequency_ghz(info, chirp), 200.0, delta=1.0)

    def test_polarizer_factor_halves(self) -> None:
        info = _info(tau_ps=16.091)
        full = rotational_frequency_ghz(info, info.nu0_thz, PulseChirp(polarizer_factor=1.0))
        half = rotational_frequency_ghz(info, info.nu0_thz, PulseChirp(polarizer_factor=0.5))
        self.assertAlmostEqual(half, 0.5 * full, places=6)


if __name__ == "__main__":
    unittest.main()
