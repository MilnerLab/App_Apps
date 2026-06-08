from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpectrumAvailable:
    """
    Published by SpectrometerService when a new spectrum slot is ready to read.

    Carries only a slot reference — no numpy data.  Consumers call
    buffer.read_spectrum_view(slot) for zero-copy access, and must call
    svc.ack_slot(slot, item_id, consumer_id) when done so the slot is
    returned to the subprocess.
    """
    slot: int
    item_id: int
    timestamp_ns: int
