from __future__ import annotations

from dataclasses import dataclass

from base_core.math.models import Angle


@dataclass(frozen=True)
class RequestRotateHwp:
    angle: Angle
