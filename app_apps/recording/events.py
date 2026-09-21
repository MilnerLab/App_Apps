from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpectrumRecordingStateChanged:
    """A standalone spectrometer recording started or stopped. Subscribers read
    ``SpectrumRecordingService.is_recording`` / ``.path``."""
