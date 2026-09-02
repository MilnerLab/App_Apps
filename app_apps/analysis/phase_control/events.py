from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StabilizationConfigChanged:
    pass


@dataclass
class PhaseTrackingStateChanged:
    pass


@dataclass
class EnvelopeStateChanged:
    pass


@dataclass
class PhaseBatchChanged:
    """The averaging block advanced, was cleared, or the loop went into settle.

    Qt-side mirror of ``messages.BatchProgress``; see it for what the fields mean.
    """
    collected: int = 0
    needed: int = 0
    coherence: float = 0.0
    capturing: bool = False
    settling: bool = False
    error_deg: float = float("nan")
