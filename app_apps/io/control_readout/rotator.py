from __future__ import annotations

from enum import StrEnum


class HwpRotator(StrEnum):
    """Which rotator carries the half-wave plate the phase loop turns.

    Both take a relative increment from the bus -- ``RequestRotateRGV`` and ``RequestRotate``
    respectively -- so the loop picks one by choosing which event it publishes.
    """
    RGV100BL = "RGV"
    ELL14 = "ELL14"
