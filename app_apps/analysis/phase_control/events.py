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
class StabilizationAutoPauseChanged:
    """Main-process mirror of the worker's StabilizationAutoPaused message, for the UI."""
    paused: bool = False
    consecutive_failures: int = 0
