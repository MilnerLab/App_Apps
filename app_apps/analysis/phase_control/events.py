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
