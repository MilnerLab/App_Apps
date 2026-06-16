"""E2E: the real probe_xcorr_scan routine against a scope-producing OpticalPlant.

probe.move_to -> scope.capture (TraceAvailable/Ack handshake, real ScopeFacade) -> mean-of-top-N
-> lab.record -> CSV. The plant's XCORR scalar is a bounded sinusoid in probe position, so the
recovered curve should peak at the plant's probe0.
"""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))

from app_apps.routines.linear.scripts.probe_scan import probe_xcorr_scan
from base_core.framework.events.event_bus import EventBus
from optical_plant import OpticalPlant, build_plant_lab


class TestXcorrPipeline(unittest.TestCase):
    def test_scan_recovers_bounded_sinusoid(self) -> None:
        plant = OpticalPlant(
            EventBus(), produce_scope=True,
            xcorr_baseline=0.1, xcorr_amp=1.0, xcorr_period_mm=4.0, xcorr_probe0_mm=2.0,
            scope_noise=0.001,
        )
        lab, _cancel = build_plant_lab(plant)
        plant.start()
        self.addCleanup(lambda: (lab.close(), plant.close()))

        with tempfile.TemporaryDirectory() as d:
            path = probe_xcorr_scan(
                lab, start_mm=0.0, stop_mm=4.0, step_mm=0.25, save_path=os.path.join(d, "x.csv")
            )
            self.assertTrue(os.path.exists(path))

        rows = lab.records
        self.assertEqual(len(rows), 17)  # frange(0, 4, 0.25) inclusive
        xs = np.array([r["probe_mm"] for r in rows])
        ys = np.array([r["xcorr"] for r in rows])
        # curve peaks at the plant's probe0 (=2.0 mm) and troughs at the band edges
        self.assertAlmostEqual(xs[int(np.argmax(ys))], 2.0, delta=0.3)
        self.assertGreater(ys.max(), 0.9)   # peak near baseline+amp = 1.1
        self.assertLess(ys.min(), 0.3)      # trough near baseline = 0.1


if __name__ == "__main__":
    unittest.main()
