"""Envelope normalisation for the soak waterfall.

The raw waterfall is dominated by the intensity bump: the fringes near the edges of the
band sit on a hundredth of the counts at the peak, so a colour map stretched over the
whole frame renders them as a uniform dark margin. Whether those fringes are *moving* --
which is the only thing the soak is for -- is invisible there while being perfectly
present in the data.

This maps the lower envelope to 0 and the upper envelope to 1, per spectrum, so every
fringe is drawn at the same contrast regardless of how much light was under it. What
survives the transform is the fringe *phase*, which is exactly what is being watched;
what is thrown away is the intensity envelope, which the panel is not asking about.

Sliding max/min rather than a fitted Gaussian pair. ``fringe_core`` fits real envelopes
because it needs their parameters; here they are only a normalisation, they have to work
on an arbitrary cropped span where the Gaussians are not identifiable (a ±2 nm ROI has
no bump in it), and this has to run on every frame of a live display. A max/min filter
one fringe period wide is the honest estimator for that job and costs one pass.

Nothing here is analysis. It changes the picture, never the file: the recorder writes raw
counts and this runs on the way to the screen.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import maximum_filter1d, minimum_filter1d, uniform_filter1d

#: Bounds on the estimated fringe period, in pixels. Below ~4 the fringes are at the
#: sampling limit and a window that narrow would track the fringes themselves instead of
#: their envelope; above n/4 there are too few periods on the span for the estimate to
#: mean anything, so a quarter of the span is the widest window worth using.
_MIN_PERIOD_PX = 4
_PERIOD_FRACTION_MAX = 0.25

#: Fraction of the row's range below which a sample is called dark and left out of the
#: period estimate. The fringes are still there in the wings -- normalisation brings them
#: back -- but they are not what the period should be measured on.
_LIT_FRACTION = 0.05

#: Amplitude below which a column is called flat and left at mid-scale. Counts, so this
#: is "the envelopes agree to within one count" -- a dark or saturated detector, where
#: dividing would turn read noise into full-scale colour.
_FLAT_EPS = 1.0


def fringe_period_px(row: np.ndarray) -> int:
    """Dominant fringe period of one spectrum, in pixels.

    By counting fringe peaks, not by an rFFT. On a full frame the intensity bump is a
    far larger Fourier component than the fringes it carries, and its skirts reach well
    past any fixed cut-off bin, so the spectral peak is the envelope and the estimate
    comes back as a quarter of the frame -- a window that normalises the fringes away
    entirely. Peak counting has no such failure: one maximum per fringe, whatever the
    envelope underneath is doing, and it behaves the same on a 2000-pixel frame and on a
    50-pixel crop, which an FFT bin cut-off cannot.

    Only the illuminated part of the row is counted. In the dark wings the fringes are
    below the noise, and counting noise maxima there would report a period of two or
    three pixels and shrink the window until it tracked the fringes instead of bounding
    them.
    """
    n = int(row.size)
    if n < 8:
        return max(_MIN_PERIOD_PX, n // 2 or 1)
    y = np.nan_to_num(np.asarray(row, dtype=np.float64))
    # Light smooth: enough to stop single-sample noise registering as a fringe, short
    # enough not to merge two real ones.
    y = uniform_filter1d(y, size=3, mode="nearest")

    lo, hi = float(y.min()), float(y.max())
    if hi - lo <= 0.0:
        return max(_MIN_PERIOD_PX, n // 20 or 1)
    lit = y >= lo + _LIT_FRACTION * (hi - lo)
    idx = np.nonzero(lit)[0]
    if idx.size < 8:
        return max(_MIN_PERIOD_PX, n // 20 or 1)
    a, b = int(idx[0]), int(idx[-1])
    seg = y[a:b + 1]
    if seg.size < 8:
        return max(_MIN_PERIOD_PX, n // 20 or 1)

    # >= on one side so a flat-topped (clipped) peak counts once, not zero times.
    peaks = int(np.count_nonzero((seg[1:-1] > seg[:-2]) & (seg[1:-1] >= seg[2:])))
    if peaks < 2:
        return max(_MIN_PERIOD_PX, n // 20 or 1)
    period = seg.size / peaks
    hi_px = max(_MIN_PERIOD_PX, int(n * _PERIOD_FRACTION_MAX))
    return int(np.clip(round(period), _MIN_PERIOD_PX, hi_px))


def envelope_normalize(block: np.ndarray, period_px: int | None = None) -> np.ndarray:
    """Map each row's lower envelope to 0 and its upper envelope to 1.

    ``block`` is [rows, pixels]; the result has the same shape and is float32 in roughly
    [0, 1]. Rows are normalised independently, so a lamp that dims over the run does not
    tilt the picture -- two spectra with the same fringes and different brightness come
    out identical, which is the entire point.

    Values are clipped a little outside [0, 1] rather than exactly to it: a fringe peak
    that sits marginally above its own sliding-max envelope is real, and hard-clipping it
    would flatten the extremes of every fringe into two solid colours.
    """
    a = np.atleast_2d(np.asarray(block, dtype=np.float64))
    if a.size == 0:
        return a.astype(np.float32)
    n_px = a.shape[1]
    if n_px < 4:
        return np.full(a.shape, 0.5, dtype=np.float32)

    if period_px is None:
        # One estimate for the whole block, from the median row: the fringe period is a
        # property of the interferometer, not of the frame, and a per-row estimate would
        # let a noisy frame renormalise itself differently from its neighbours -- which
        # would show up as horizontal banding that is an artefact of this function.
        period_px = fringe_period_px(np.median(a, axis=0))
    # A shade over one period: a window of exactly one period leaves the sliding max
    # touching the same fringe peak twice at the turn, which ripples the envelope.
    win = int(np.clip(round(period_px * 1.2), _MIN_PERIOD_PX, max(_MIN_PERIOD_PX, n_px)))

    # 'nearest' at the edges: 'reflect' would mirror a fringe back on itself and invent an
    # envelope that pinches to the signal in the last half-period.
    upper = maximum_filter1d(a, size=win, axis=1, mode="nearest")
    lower = minimum_filter1d(a, size=win, axis=1, mode="nearest")
    # The max/min of a sampled sinusoid is a staircase; smoothing over the same window
    # turns it back into the smooth bound it is standing in for.
    upper = uniform_filter1d(upper, size=win, axis=1, mode="nearest")
    lower = uniform_filter1d(lower, size=win, axis=1, mode="nearest")

    amp = upper - lower
    flat = amp < _FLAT_EPS
    out = np.empty_like(a)
    np.divide(a - lower, np.where(flat, 1.0, amp), out=out)
    out[flat] = 0.5
    np.clip(out, -0.15, 1.15, out=out)
    return out.astype(np.float32)
