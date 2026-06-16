"""E2E: the scan routines (probe_scan_with_spectrum, overnight_central_freq_series) against the
plant with both spectrum + scope producers. Exercises the scan + fit + CSV path over many points.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))

from app_apps.routines.linear.scripts.probe_scan import (
    overnight_central_freq_series,
    probe_scan_with_spectrum,
)
from base_core.framework.events.event_bus import EventBus
from optical_plant import OpticalPlant, build_plant_lab


class _ScanCase(unittest.TestCase):
    def _make(self, **kw) -> OpticalPlant:
        plant = OpticalPlant(EventBus(), produce_scope=True, nu0_base=374.0, nu0_slope=3.0, **kw)
        self.plant = plant
        self.lab, _cancel = build_plant_lab(plant)
        plant.start()
        self.addCleanup(lambda: (self.lab.close(), plant.close()))
        return plant


class TestProbeScanWithSpectrum(_ScanCase):
    def test_records_xcorr_and_fit(self) -> None:
        self._make()  # delay stays at 0 -> nu0 = 374 THz throughout the probe sweep
        with tempfile.TemporaryDirectory() as d:
            path = probe_scan_with_spectrum(
                self.lab, start_mm=0.0, stop_mm=1.0, step_mm=0.5, save_path=os.path.join(d, "s.csv")
            )
            self.assertTrue(os.path.exists(path))
        rows = self.lab.records
        self.assertEqual(len(rows), 3)  # frange(0, 1, 0.5)
        for r in rows:
            self.assertEqual(set(r), {"probe_mm", "xcorr", "nu0_thz", "span_thz"})
            self.assertAlmostEqual(r["nu0_thz"], 374.0, delta=0.3)
            self.assertGreater(r["span_thz"], 0.0)


class TestOvernightSeries(_ScanCase):
    def test_tags_rows_per_delay(self) -> None:
        self._make()
        with tempfile.TemporaryDirectory() as d:
            path = overnight_central_freq_series(
                self.lab, delay_setpoints_mm=[0.0, 1.0],
                start_mm=0.0, stop_mm=0.5, step_mm=0.5, save_path=os.path.join(d, "o.csv"),
            )
            self.assertTrue(os.path.exists(path))
        rows = self.lab.records
        self.assertEqual(len(rows), 4)  # 2 delays x frange(0, 0.5, 0.5)=2 probe points
        self.assertEqual(sorted({r["delay_mm"] for r in rows}), [0.0, 1.0])
        for r in rows:
            self.assertEqual(set(r), {"delay_mm", "probe_mm", "xcorr"})


if __name__ == "__main__":
    unittest.main()
