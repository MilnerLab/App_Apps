"""Unit tests for the append-only HDF5 calibration store."""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.analysis.xcorr.store import CalibrationStore, new_calibration


def _cal(when: str, delay_pos: float = 3.0):
    return new_calibration(
        grating_stage="esp100",
        grating_position=12.0,
        delay_stage="esp301",
        delay_position=delay_pos,
        wavelengths_nm=np.array([800.0, 805.0, 810.0]),
        delays_ps=np.array([0.0, 1.0, 2.0]),
        created_utc=when,
    )


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "calib.h5")
        self.store = CalibrationStore(self.path)

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass
        os.rmdir(self.dir)

    def test_append_never_overwrites(self):
        # Two calibrations for the SAME combination + same timestamp -> both retained.
        k1 = self.store.append(_cal("2026-06-12T10:00:00+00:00"))
        k2 = self.store.append(_cal("2026-06-12T10:00:00+00:00"))
        self.assertNotEqual(k1, k2)
        self.assertEqual(self.store.count(), 2)

    def test_load_round_trip(self):
        key = self.store.append(_cal("2026-06-12T10:00:00+00:00"))
        loaded = self.store.load(key)
        self.assertEqual(loaded.grating_stage, "esp100")
        self.assertEqual(loaded.delay_stage, "esp301")
        np.testing.assert_allclose(loaded.wavelengths_nm, [800.0, 805.0, 810.0])
        np.testing.assert_allclose(loaded.delays_ps, [0.0, 1.0, 2.0])

    def test_entries_newest_first(self):
        self.store.append(_cal("2026-06-12T10:00:00+00:00"))
        self.store.append(_cal("2026-06-12T12:00:00+00:00"))
        self.store.append(_cal("2026-06-12T11:00:00+00:00"))
        times = [e.created_utc for e in self.store.entries()]
        self.assertEqual(times[0], "2026-06-12T12:00:00+00:00")
        self.assertEqual(times[-1], "2026-06-12T10:00:00+00:00")

    def test_latest_and_filter(self):
        self.store.append(_cal("2026-06-12T10:00:00+00:00"))
        self.store.append(_cal("2026-06-12T12:00:00+00:00", delay_pos=9.0))
        latest = self.store.latest()
        self.assertEqual(latest.created_utc, "2026-06-12T12:00:00+00:00")
        self.assertEqual(latest.delay_position, 9.0)
        # filter that matches
        self.assertIsNotNone(self.store.latest(grating_stage="esp100"))
        # filter that doesn't match
        self.assertIsNone(self.store.latest(grating_stage="nope"))


if __name__ == "__main__":
    unittest.main()
