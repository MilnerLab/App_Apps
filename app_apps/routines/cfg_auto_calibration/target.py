"""The operator's frequency target and its two equivalent representations.

The centrifuge sweep is two numbers. The operator may enter them either way:

  * START / END  -- the frequencies at the two temporal edges of the window.
  * CENTER / BW  -- the central frequency and the swept range (bandwidth).

The two are related by
      center = (start + end) / 2          start = center - bw / 2
      bw     =  end - start               end   = center + bw / 2

The solver always consumes CENTER / BW (f0, df), which is the pair the calibration model
inverts. The toggle is therefore a pure display transform over the same underlying target.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TargetMode(Enum):
    """How the two frequency fields are interpreted."""

    START_END = "start_end"
    CENTER_BANDWIDTH = "center_bandwidth"


@dataclass(frozen=True)
class CfgTarget:
    """A frequency sweep target, stored canonically as (center, bandwidth) in Hz."""

    center_hz: float
    bandwidth_hz: float

    # -- construction from either representation ------------------------------
    @classmethod
    def from_center_bandwidth(cls, center_hz: float, bandwidth_hz: float) -> "CfgTarget":
        return cls(center_hz=center_hz, bandwidth_hz=bandwidth_hz)

    @classmethod
    def from_start_end(cls, start_hz: float, end_hz: float) -> "CfgTarget":
        return cls(center_hz=0.5 * (start_hz + end_hz), bandwidth_hz=end_hz - start_hz)

    @classmethod
    def from_fields(cls, mode: TargetMode, a_hz: float, b_hz: float) -> "CfgTarget":
        """Build from the two raw field values, interpreted per ``mode``."""
        if mode is TargetMode.START_END:
            return cls.from_start_end(a_hz, b_hz)
        return cls.from_center_bandwidth(a_hz, b_hz)

    # -- projection back to either representation -----------------------------
    @property
    def start_hz(self) -> float:
        return self.center_hz - 0.5 * self.bandwidth_hz

    @property
    def end_hz(self) -> float:
        return self.center_hz + 0.5 * self.bandwidth_hz

    def fields(self, mode: TargetMode) -> tuple[float, float]:
        """The two field values (a, b) for the given display mode."""
        if mode is TargetMode.START_END:
            return self.start_hz, self.end_hz
        return self.center_hz, self.bandwidth_hz
