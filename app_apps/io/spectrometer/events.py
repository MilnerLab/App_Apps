from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpectrometerConfigChanged:
    """Published when the user applies new spectrometer settings."""


@dataclass(frozen=True)
class SpectrometerWorkerStateChanged:
    """Published whenever the spectrometer handle's WorkerStatus changes.
    Subscribers read handle.state to get the current WorkerStatus."""


@dataclass(frozen=True)
class SpectrumAvailable:
    """
    Published by SpectrometerService when a new spectrum slot is ready.

    Broadcast to all registered consumers — each reads the same slot from
    shared memory and acks with their own consumer_id.
    """
    slot: int
    item_id: int
    timestamp_ns: int


@dataclass(frozen=True)
class SpectrumAck:
    """Published by a consumer after it finishes reading a spectrum slot."""
    slot: int
    item_id: int
    consumer_id: str
