from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpectrumAvailable:
    """
    Published by SpectrometerService when a new spectrum slot is ready for a consumer.

    consumer_id identifies which registered consumer should process this event;
    VMs filter on their own CONSUMER_ID to avoid duplicate reads when multiple
    consumers are registered.

    After processing, publish SpectrumAck with the same slot/item_id/consumer_id.
    """
    slot: int
    item_id: int
    timestamp_ns: int
    consumer_id: str = ""


@dataclass(frozen=True)
class SpectrumAck:
    """Published by a consumer after it finishes reading a spectrum slot."""
    slot: int
    item_id: int
    consumer_id: str
