from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OscilloscopeWorkerStateChanged:
    """Published on every OscilloscopeWorkerHandle status transition (no payload).

    Subscribers read ``handle.state`` for the current ``WorkerStatus``. Replaces the
    slot-based ``TraceAvailable``/``TraceAck`` of the deleted shared-memory path (B14):
    acquisition is now request/reply, so there are no trace slots to advertise.
    """
