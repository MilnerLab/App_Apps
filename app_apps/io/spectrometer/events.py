from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpectrumAvailable:
    """
    Published by SpectrometerService when a new spectrum slot is ready for a consumer.

    consumer_id identifies which registered consumer should process this event;
    VMs filter on their own CONSUMER_ID to avoid duplicate reads when multiple
    consumers are registered.

    The consumer calls buffer.read_spectrum_view(slot) for zero-copy access and
    svc.ack_slot(slot, item_id, consumer_id) when done.
    """
    slot: int
    item_id: int
    timestamp_ns: int
    consumer_id: str = ""
