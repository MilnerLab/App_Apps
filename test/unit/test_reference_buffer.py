"""Unit tests for the ReferenceBuffer (M2.2) — pure numpy, no hardware."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.analysis.spectrum_info.reference import ReferenceBuffer


def _frame(intensity: float, n: int = 4) -> np.ndarray:
    """A (2, n) spectrum frame: row 0 wavelengths, row 1 a flat intensity."""
    wl = np.linspace(700.0, 900.0, n)
    return np.vstack([wl, np.full(n, intensity)])


class TestReferenceBuffer(unittest.TestCase):
    def test_history_is_bounded_and_ordered(self) -> None:
        buf = ReferenceBuffer(history=3)
        for i in range(5):
            buf.add(_frame(float(i)))
        self.assertEqual(len(buf), 3)
        # oldest two dropped -> intensities 2,3,4 oldest-first
        intensities = [f[1, 0] for f in buf.recent]
        self.assertEqual(intensities, [2.0, 3.0, 4.0])
        self.assertEqual(buf.latest[1, 0], 4.0)

    def test_rejects_wrong_shape(self) -> None:
        buf = ReferenceBuffer()
        with self.assertRaises(ValueError):
            buf.add(np.zeros((3, 4)))
        with self.assertRaises(ValueError):
            buf.add(np.zeros(4))

    def test_set_reference_explicit_and_from_history(self) -> None:
        buf = ReferenceBuffer()
        self.assertFalse(buf.has_reference)
        buf.set_reference(_frame(7.0))
        self.assertTrue(buf.has_reference)
        self.assertEqual(buf.reference[1, 0], 7.0)

        buf.add(_frame(9.0))
        buf.set_reference()  # promote most recent
        self.assertEqual(buf.reference[1, 0], 9.0)

    def test_set_reference_without_history_raises(self) -> None:
        buf = ReferenceBuffer()
        with self.assertRaises(ValueError):
            buf.set_reference()

    def test_mean(self) -> None:
        buf = ReferenceBuffer()
        self.assertIsNone(buf.mean())
        buf.add(_frame(2.0))
        buf.add(_frame(4.0))
        self.assertEqual(buf.mean()[1, 0], 3.0)

    def test_reset_clears_everything(self) -> None:
        buf = ReferenceBuffer()
        buf.add(_frame(1.0))
        buf.set_reference(_frame(1.0))
        buf.reset()
        self.assertEqual(len(buf), 0)
        self.assertFalse(buf.has_reference)
        self.assertIsNone(buf.latest)

    def test_stored_frames_are_copies(self) -> None:
        buf = ReferenceBuffer()
        f = _frame(1.0)
        buf.add(f)
        buf.set_reference(f)
        f[1, :] = 999.0  # mutate caller's array after storing
        self.assertEqual(buf.latest[1, 0], 1.0)
        self.assertEqual(buf.reference[1, 0], 1.0)
        # the returned copy is also independent
        got = buf.latest
        got[1, :] = -1.0
        self.assertEqual(buf.latest[1, 0], 1.0)

    def test_invalid_history(self) -> None:
        with self.assertRaises(ValueError):
            ReferenceBuffer(history=0)


if __name__ == "__main__":
    unittest.main()
