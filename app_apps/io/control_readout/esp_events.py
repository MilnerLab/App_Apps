"""App-level (main-bus) events for commanding the ESP301 stages.

Kept in a separate module (additive) so we don't edit the contributor's
``control_readout/events.py``. These are plain dataclasses published on the main
EventBus; the :class:`EspHandle` bridges them to ESP301 IPC requests. Position
telemetry flows the other way as ``control_readout.esp_301.messages.PositionUpdate``,
which the service auto-publishes on the bus (subscribe to it directly).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestMove:
    """Request an absolute move of one ESP301 axis."""

    axis: int
    position: float
