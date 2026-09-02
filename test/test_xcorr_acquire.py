"""Unit tests for the XCORR acquisition path: the reduction (B9) and the mock (B8).

Covers the two decisive, hardware-free pieces of Build Step 2:

* ``oscilloscope.reduce.positive_mean`` — the within-trace reduction (D3 step 1),
  incl. all-negative, all-zero and single-positive edges, and the across-vs-within
  distinction that D3 exists to enforce.
* ``oscilloscope.mock_driver.MockScope`` — a position-dependent synthetic signal
  whose positive-mean peaks at the overlap centre and falls off away from it, a
  monotonic ``NUMACq?`` counter, and fresh (differing) consecutive traces.

No pytest (AGENTS.md §5). Run directly —

    App_Apps\\.venv\\Scripts\\python.exe App_Apps\\test\\test_xcorr_acquire.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oscilloscope.config import ScopeConfig  # noqa: E402
from oscilloscope.mock_driver import MockScope  # noqa: E402
from oscilloscope.reduce import positive_mean  # noqa: E402


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


# --- positive_mean (D3 step 1) --------------------------------------------

def test_positive_mean_all_negative_is_zero():
    mean, count = positive_mean(np.array([-1.0, -2.0, -0.5]))
    assert mean == 0.0 and count == 0, (mean, count)


def test_positive_mean_all_zero_is_zero():
    # zero is not strictly positive — excluded, so no positive samples.
    mean, count = positive_mean(np.zeros(10))
    assert mean == 0.0 and count == 0, (mean, count)


def test_positive_mean_single_positive_sample():
    mean, count = positive_mean(np.array([-3.0, 0.0, 7.0, -1.0]))
    assert approx(mean, 7.0) and count == 1, (mean, count)


def test_positive_mean_mixed():
    # positives are 2 and 4 -> mean 3, count 2; negatives and zero ignored.
    mean, count = positive_mean(np.array([2.0, -5.0, 4.0, 0.0, -1.0]))
    assert approx(mean, 3.0) and count == 2, (mean, count)


def test_within_vs_between_distinction():
    """D3: per-trace positive-mean then average, not a global positive-mean.

    Two traces with very different positive counts must be weighted equally by the
    across-trace step, unlike a naive pooled positive-mean which weights by count.
    """
    t1 = np.array([10.0, -1.0, -1.0, -1.0])          # one positive: within-mean 10
    t2 = np.array([2.0, 2.0, 2.0, 2.0])              # four positives: within-mean 2
    m1, _ = positive_mean(t1)
    m2, _ = positive_mean(t2)
    across = float(np.mean([m1, m2]))                # D3: (10 + 2)/2 = 6
    pooled = float(np.concatenate([t1, t2])[np.concatenate([t1, t2]) > 0].mean())  # naive: 18/5=3.6
    assert approx(across, 6.0), across
    assert not approx(across, pooled), (across, pooled)


# --- MockScope (B8) -------------------------------------------------------

def _reduce_point(scope: MockScope, probe_mm: float, n: int, channel: int = 1) -> float:
    scope.set_context(probe_mm)
    vals = []
    for _ in range(n):
        scope.numacq()
        vals.append(positive_mean(scope.read_trace(channel))[0])
    return float(np.mean(vals))


def test_mock_signal_peaks_at_overlap_centre():
    cfg = ScopeConfig(mock=True, mock_center_mm=154.0, mock_width_mm=3.0,
                      mock_noise=0.0, mock_seed=1)
    scope = MockScope(cfg)
    at_centre = _reduce_point(scope, 154.0, 8)
    off_centre = _reduce_point(scope, 154.0 + 10.0, 8)
    assert at_centre > off_centre > 0.0 or (at_centre > 0 and off_centre >= 0), (at_centre, off_centre)
    # far off the overlap the signal is essentially gone
    assert off_centre < 0.25 * at_centre, (at_centre, off_centre)


def test_mock_numacq_is_monotonic():
    scope = MockScope(ScopeConfig(mock=True))
    seq = [scope.numacq() for _ in range(5)]
    assert seq == [1, 2, 3, 4, 5], seq


def test_mock_consecutive_traces_differ():
    scope = MockScope(ScopeConfig(mock=True, mock_noise=0.01, mock_seed=7))
    scope.set_context(154.0)
    a = scope.read_trace(1)
    b = scope.read_trace(1)
    assert not np.array_equal(a, b), "free-running mock must not return an identical buffer"
    assert a.shape == (ScopeConfig().n_samples,), a.shape


def test_mock_record_length_is_2500():
    scope = MockScope(ScopeConfig(mock=True))
    assert scope.read_trace(1).shape == (2500,)


# --- runner ---------------------------------------------------------------

def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
        else:
            print(f"ok    {t.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
