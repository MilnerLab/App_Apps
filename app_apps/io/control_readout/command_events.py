"""App-level (main-bus) command events for the RGV100BL / picomotor / servo workers.

Kept separate (additive) so we don't edit the contributor's ``control_readout/events.py``.
Each handle bridges these to the corresponding IPC request; typed handle methods are the
primary API for routines/VMs. Telemetry flows back as the workers' ``Message`` events
(e.g. ``HwpAngleUpdate``), which the service auto-publishes on the bus.
"""
from __future__ import annotations

from dataclasses import dataclass

from base_core.math.models import Angle


@dataclass(frozen=True)
class RequestRotateHwp:
    angle: Angle


@dataclass(frozen=True)
class RequestStepPicomotor:
    axis: int
    steps: int


@dataclass(frozen=True)
class RequestSetArmBlocked:
    arm: int
    blocked: bool
