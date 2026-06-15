"""Reference-spectrum buffer (M2.2) — a single drift baseline + a short history of raw spectra.

The reference is a *single-arm* snapshot used as a drift baseline (one centrifuge arm blocked);
the history is a small rolling window of the most recent raw spectra, handy for averaging or
"compare to N seconds ago". Both store **copies** of the raw frame, because the spectrum shared
buffer reuses its backing array between captures.

A raw spectrum frame matches the `SpectrumBuffer` layout used across the codebase: shape
`(2, pixel_count)` with row 0 = wavelengths (nm) and row 1 = intensities. This module is pure
(numpy only) and has no device/routine dependencies, so it is trivially unit-testable.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional, Tuple

import numpy as np


def _validate(frame: np.ndarray) -> np.ndarray:
    arr = np.asarray(frame, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] != 2:
        raise ValueError(
            f"spectrum frame must have shape (2, pixel_count) "
            f"(row 0 wavelengths, row 1 intensities); got {arr.shape}"
        )
    return arr


class ReferenceBuffer:
    """Holds one reference spectrum plus a deque of the most recent raw spectra.

    - `add(frame)` pushes a raw spectrum into the rolling history (oldest dropped past `history`).
    - `set_reference(frame=None)` captures a drift baseline; with no argument it promotes the most
      recent spectrum in the history (raises if the history is empty).
    - `reset()` clears the reference and the history (the M2.2 "reset event").

    All stored frames are independent copies.
    """

    def __init__(self, history: int = 5) -> None:
        if history < 1:
            raise ValueError(f"history must be >= 1, got {history}")
        self._history = history
        self._recent: Deque[np.ndarray] = deque(maxlen=history)
        self._reference: Optional[np.ndarray] = None

    # --- history -------------------------------------------------------------------------
    def add(self, frame: np.ndarray) -> None:
        """Append a raw spectrum to the rolling history (stored as a copy)."""
        self._recent.append(_validate(frame).copy())

    @property
    def recent(self) -> Tuple[np.ndarray, ...]:
        """The buffered spectra, oldest first, newest last (copies)."""
        return tuple(f.copy() for f in self._recent)

    @property
    def latest(self) -> Optional[np.ndarray]:
        """The most recently added spectrum, or None if the history is empty (a copy)."""
        return self._recent[-1].copy() if self._recent else None

    def mean(self) -> Optional[np.ndarray]:
        """Element-wise mean of the buffered spectra (None if empty).

        Wavelength rows are assumed aligned across captures (same spectrometer pixels), so the
        averaged row 0 equals the common wavelength axis and row 1 is the averaged intensity.
        """
        if not self._recent:
            return None
        return np.mean(np.stack(self._recent), axis=0)

    # --- reference -----------------------------------------------------------------------
    def set_reference(self, frame: Optional[np.ndarray] = None) -> None:
        """Capture a drift baseline. With no frame, promote the most recent buffered spectrum."""
        if frame is None:
            if not self._recent:
                raise ValueError("no spectrum to use as reference (history is empty)")
            self._reference = self._recent[-1].copy()
        else:
            self._reference = _validate(frame).copy()

    @property
    def reference(self) -> Optional[np.ndarray]:
        """The current reference baseline, or None if none captured (a copy)."""
        return self._reference.copy() if self._reference is not None else None

    @property
    def has_reference(self) -> bool:
        return self._reference is not None

    # --- reset ---------------------------------------------------------------------------
    def reset(self) -> None:
        """Clear the reference and the history (M2.2 reset event)."""
        self._recent.clear()
        self._reference = None

    def __len__(self) -> int:
        return len(self._recent)
