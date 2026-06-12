from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceConfig:
    spectrometer: bool = False
    rotator: bool = False
    phase_control: bool = False
