"""Closed-loop integration tests: real control chain vs the stateful OpticalPlant.

What this exercises (all real, in-process): a free-running mock spectrometer whose emitted
spectrum responds to the actuator -> the shared-memory handshake -> the lmfit spectrum fit ->
the PID engine -> the control loop -> actuation that changes the next spectrum.

Findings baked into the test structure (see also docs / the demo script):
  * delay -> nu0 and truncation -> nu_end close cleanly: these observables come from the
    spectrum *envelope*, which the cold-start fit recovers robustly.
  * HWP -> phase0 does NOT close with a cold-start fit (`TestPhaseFitDiagnostic`): the fitter
    cannot recover `phase0` from a fringed spectrum cold (it diverges). Warm-starting the fit
    with the previous result fixes it, and the HWP loop then closes (`TestPhaseLoopWarmStart`).
    The packaged `lock_phase` fits cold -> it needs a small warm-start change to be viable.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))

from app_apps.analysis.spectrum_info.fit import fit_spectrum
from app_apps.analysis.spectrum_info.generator import synthetic_spectrum, wavelength_grid
from app_apps.analysis.spectrum_info.model import (
    SpectrumParams,
    envelope_edges_thz,
)
from app_apps.routines.linear.scripts.control_loops import (
    lock_central_frequency,
    lock_terminal_frequency,
)
from base_core.framework.events.event_bus import EventBus
from optical_plant import OpticalPlant, build_plant_lab, warm_phase_lock


class _PlantLoopCase(unittest.TestCase):
    def _make(self, **plant_kw) -> OpticalPlant:
        plant = OpticalPlant(EventBus(), **plant_kw)
        self.plant = plant
        self.lab, self.cancel = build_plant_lab(plant)
        plant.start()
        self.addCleanup(self._teardown)
        return plant

    def _teardown(self) -> None:
        self.lab.close()
        self.plant.close()


class TestLockCentralFrequency(_PlantLoopCase):
    """delay -> nu0 closes through the full real chain (envelope observable)."""

    def test_converges(self) -> None:
        self._make(nu0_base=374.0, nu0_slope=3.0)  # positive slope -> default-positive PID
        target = 380.0
        result = lock_central_frequency(
            self.lab, target_thz=target, kp=0.15, tolerance_thz=0.05,
            max_iterations=80, dt_s=0.05,
        )
        self.assertTrue(result.converged, f"did not converge: {result}")
        self.assertAlmostEqual(self.plant.state().nu0_thz, target, delta=0.1)
        self.assertGreater(result.iterations, 0)


class TestLockTerminalFrequency(_PlantLoopCase):
    """truncation -> nu_end closes; true-state delta is looser (red-edge fit is less precise)."""

    def test_converges(self) -> None:
        self._make(nu0_base=374.0, nu0_slope=3.0, bw_base=30.0, bw_slope=-4.0)
        start = self.plant.state().nu_end_thz
        target = start + 2.5
        result = lock_terminal_frequency(
            self.lab, target_thz=target, kp=0.3, tolerance_thz=0.05,
            max_iterations=80, dt_s=0.05,
        )
        self.assertTrue(result.converged, f"did not converge (measured): {result}")
        # The loop converges on the *measured* nu_end. The red edge = C/(lambda0 + 2 sigma) is an
        # envelope-*width* quantity, fit less precisely than the center (nu0) -> ~0.5 THz of
        # systematic bias between fitted and true. So assert the loop drove the *true* nu_end most
        # of the way to target, not that it lands within a tiny band.
        true_final = self.plant.state().nu_end_thz
        self.assertLess(abs(true_final - target), 0.4 * abs(start - target),
                        f"true nu_end {true_final:.3f} barely moved from {start:.3f} toward {target:.3f}")


class TestPhaseLoopWarmStart(_PlantLoopCase):
    """HWP -> phase0 closes when the fit is warm-started (the fix the diagnostic motivates).

    Drives the real PID engine (`run_pid_lock`) and the real `lab.hwp` actuation + real
    `lab.spectrometer.read()`; only the measurement warm-starts the fit (carrying the previous
    result as `init`) instead of the cold `lab.fit_spectrum`.
    """

    def test_converges(self) -> None:
        plant = self._make(tau_ps=0.15, phase_off=0.05, phase_gain=1.0)
        target = 0.80
        # First-fit init: approximate chirp/envelope known to the experimenter; phase rough.
        fit_init = SpectrumParams(
            central_wavelength_nm=801.6, bandwidth_nm=30.0, amp_upper=1.0, amp_lower=0.05,
            phase0=0.0, tau_ps=0.15, g2=0.0, g3=0.0,
        )
        result = warm_phase_lock(self.lab, target_rad=target, fit_init=fit_init, kp=0.5)
        self.assertTrue(result.converged, f"did not converge: {result}")
        self.assertAlmostEqual(plant.state().phase0, target, delta=0.05)


class TestPhaseFitDiagnostic(unittest.TestCase):
    """Pin the finding: cold fit fails on a fringed spectrum; warm-started fit recovers phase0."""

    def setUp(self) -> None:
        self.grid = wavelength_grid(760.0, 840.0, 512)
        self.true = SpectrumParams(
            central_wavelength_nm=801.6, bandwidth_nm=30.0, amp_upper=1.0, amp_lower=0.05,
            phase0=0.8, tau_ps=0.15, g2=0.0, g3=0.0,
        )
        self.intensities = synthetic_spectrum(self.grid, self.true, noise=0.005, seed=3)

    def test_cold_fit_fails_on_fringes(self) -> None:
        info = fit_spectrum(self.grid, self.intensities)  # cold
        # The cold fit cannot lock onto the fringe chirp -> a large residual (a bad fit).
        self.assertGreater(info.fit_residual, 0.05)

    def test_warm_fit_recovers_phase0(self) -> None:
        init = SpectrumParams(
            central_wavelength_nm=801.6, bandwidth_nm=30.0, amp_upper=1.0, amp_lower=0.05,
            phase0=0.0, tau_ps=0.15, g2=0.0, g3=0.0,  # phase0 deliberately wrong
        )
        info = fit_spectrum(self.grid, self.intensities, init=init)
        self.assertAlmostEqual(info.phase0, self.true.phase0, delta=0.02)
        self.assertLess(info.fit_residual, 0.02)


class TestFitAccuracy(unittest.TestCase):
    """Envelope observables (nu0, nu_end) are recovered by the cold fit."""

    def test_recovers_envelope_frequencies(self) -> None:
        grid = wavelength_grid(760.0, 840.0, 512)
        params = SpectrumParams(
            central_wavelength_nm=801.6, bandwidth_nm=30.0, amp_upper=1.0, amp_lower=0.05,
            phase0=0.8, tau_ps=0.15, g2=0.0, g3=0.0,
        )
        intensities = synthetic_spectrum(grid, params, noise=0.01, seed=7)
        info = fit_spectrum(grid, intensities)
        nu0, _start, nu_end = envelope_edges_thz(params)
        self.assertAlmostEqual(info.nu0_thz, nu0, delta=0.1)
        self.assertAlmostEqual(info.nu_end_thz, nu_end, delta=0.5)


if __name__ == "__main__":
    unittest.main()
