"""Unit tests for the mock oscilloscope driver (synthetic traces, no hardware)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from oscilloscope.config import ScopeConfig
from oscilloscope.mock_driver import MockScope, ScopeTrace


class TestMockScope(unittest.TestCase):
    def test_trace_shape_and_timestamp(self):
        tr = MockScope(ScopeConfig(channels=2, n_samples=500, mock_seed=0)).acquire_trace()
        self.assertIsInstance(tr, ScopeTrace)
        self.assertEqual(tr.samples.shape, (2, 500))
        self.assertGreater(tr.timestamp_ns, 0)

    def test_single_channel(self):
        tr = MockScope(ScopeConfig(channels=1, n_samples=256, mock_seed=0)).acquire_trace()
        self.assertEqual(tr.samples.shape, (1, 256))

    def test_ch1_envelope_bounded(self):
        tr = MockScope(ScopeConfig(n_samples=1000, mock_noise=0.0, mock_seed=0)).acquire_trace()
        ch1 = tr.samples[0]
        # envelope-bounded sinusoid with zero noise: values in [0, 1]
        self.assertGreaterEqual(ch1.min(), -1e-9)
        self.assertLessEqual(ch1.max(), 1.0 + 1e-9)

    def test_reproducible_with_seed(self):
        a = MockScope(ScopeConfig(mock_seed=42)).acquire_trace().samples
        b = MockScope(ScopeConfig(mock_seed=42)).acquire_trace().samples
        np.testing.assert_allclose(a, b)

    def test_ch2_is_position_ramp(self):
        tr = MockScope(ScopeConfig(n_samples=100, mock_seed=0)).acquire_trace()
        ch2 = tr.samples[1]
        self.assertTrue(np.all(np.diff(ch2) > 0))  # monotonic ramp


if __name__ == "__main__":
    unittest.main()
