"""Optimizer-free fringe-contrast index — the cheap gate in front of the cold fit.

**Why this is not in ``fringe_core.py``.** That file is a verbatim copy of the standalone
and may not be patched on this side (see ``fringe_fit``'s header). This metric is also not
analysis: it decides whether the control loop is allowed to *act*, which is loop policy.
It lives App-side for both reasons.

**The failure it exists to stop.** While instrument settings change, the fringes fly and
average away, leaving a clean bright Gaussian with no oscillation. Measured on a synthetic
800 nm trace (700 px, 300 counts over a 155-count floor, read noise sigma 4),
``fringe_core.analyze`` on that frame costs **46.7 seconds** and returns ``status="ok"`` --
it fits noise, passes its own gates, and hands the loop a confident phase with no physical
basis. The second half is the worse one. The existing ``dead_window`` guard does not catch
it: its thresholds (``DEAD_GAP_FRAC=1e-3``, ``DEAD_OSC_STD=1e-6``) are "mathematically
zero", not "physically useless", and it only runs AFTER two Nelder-Mead envelope fits, i.e.
after the cost has been paid.

    V_true   fit time    V_meas   verdict at 0.12
    0.60       381 ms    0.7638   fit
    0.15       260 ms    0.2461   fit
    0.08       357 ms    0.1398   fit
    0.04    12 523 ms    0.0805   ABORT
    0.02    21 496 ms    0.0504   ABORT
    0.00    46 718 ms    0.0458   ABORT

Cost ~1.9 ms, against a 260 ms good fit (0.7% overhead) and a 47 000 ms bad one.

**It is a contrast INDEX, not a calibrated visibility.** It reads biased high (V_true 0.60
-> 0.76). That is fine and deliberate: it is a gate, so monotonicity is all that is
required, and it is monotone across the whole sweep. Do not quote it as a visibility.

The noise subtraction is what makes it usable at all -- without it the estimator floors out
at ~0.04 on a fringe-free trace and the good/bad classes stop separating. It is deliberately
NOT an SNR: an SNR measure was tried first and rejected because it tracked brightness rather
than contrast. Light-level dependence was checked at 10x and 33x dimmer and the index is
light-independent down to ~30 counts, well below normal operation.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d, uniform_filter1d

# Window lengths as a fraction of the trace, for the envelope midline and the AC power.
# Wide enough to average many fringes, narrow enough to follow the Gaussian bump.
_SMOOTH_FRAC = 0.05
# The continuum reference: a low percentile of the trace, i.e. the fringe minima floor.
_CONTINUUM_PCT = 5.0
# Only pixels carrying real envelope are allowed to vote, so the wings -- where the ratio
# is 0/0 -- cannot drag the median around.
_CORE_FRAC = 0.5
# MAD -> sigma for a normal, and the second difference of white noise has variance 6*sigma^2.
_MAD_TO_SIGMA = 1.4826
_D2_VARIANCE = 6.0

# Default gate. A factor ~1.2 below the last good point (V_meas 0.1398 at V_true 0.08) and
# ~1.5 above the fringe-free floor (0.0458). See the table above.
MIN_VISIBILITY = 0.12


def fringe_visibility(intensities: np.ndarray) -> float:
    """Contrast index of a raw trace in [0, inf). Returns 0.0 on a degenerate input.

    No optimizer, no fit, no seed: two filter passes and a median-absolute-deviation noise
    estimate. Pass the RAW trace -- windowing first is allowed but not required, since the
    core mask below already discards the wings.
    """
    y = np.asarray(intensities, dtype=float)
    y = y[np.isfinite(y)]
    n = y.size
    if n < 16:
        return 0.0

    w = max(int(round(_SMOOTH_FRAC * n)), 3)

    # Noise from the SECOND DIFFERENCE: fringes are smooth pixel-to-pixel and read noise is
    # not, so d2 is almost pure noise even on a strongly modulated trace.
    d2 = np.diff(y, n=2)
    sigma_n = _MAD_TO_SIGMA * float(np.median(np.abs(d2 - np.median(d2)))) / np.sqrt(_D2_VARIANCE)

    dc = gaussian_filter1d(y, w)                       # envelope midline
    ac2 = uniform_filter1d((y - dc) ** 2, w)           # = A^2/2 + sigma_n^2
    amp = np.sqrt(np.clip(2.0 * (ac2 - sigma_n ** 2), 0.0, None))   # noise-corrected amp

    above = dc - float(np.percentile(y, _CONTINUUM_PCT))            # envelope over continuum
    peak = float(np.max(above)) if above.size else 0.0
    if not np.isfinite(peak) or peak <= 0.0:
        return 0.0

    core = above > _CORE_FRAC * peak
    if not core.any():
        return 0.0
    return float(np.median(amp[core] / above[core]))
