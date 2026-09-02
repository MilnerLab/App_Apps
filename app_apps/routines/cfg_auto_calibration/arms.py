"""Arm/stage metadata for the CFG auto-calibration panel.

Three ESP301 axes, all on COM7 behind one serial lock (moves are serialized):

    axis 1  FMS300PP  PROBE    -9.5 .. 290.5 mm   (xcorr scan axis; manual control only)
    axis 2  MFA-CC    DELAY     0.0 .. 25.0  mm   (sets the central frequency)
    axis 3  UTS150CC  GRATING -75.0 .. 75.0  mm   (sets the swept bandwidth)

Soft limits mirror the live values verified in XCORR_SPEC.md sec. 3.1. Every commanded
position is validated against these before a move is dispatched, so a bad target fails in
the UI and never becomes an over-travel fault at the controller.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Arm(Enum):
    GRATING = "grating"
    DELAY = "delay"
    PROBE = "probe"


@dataclass(frozen=True)
class ArmSpec:
    arm: Arm
    label: str          # UI label
    stage: str          # device model, for provenance/tooltips
    axis: int           # 1-based ESP301 axis
    limit_min_mm: float
    limit_max_mm: float
    step_fine_mm: float    # the `<` / `>` jog
    step_coarse_mm: float  # the `<<` / `>>` jog

    def clamp(self, mm: float) -> float:
        return max(self.limit_min_mm, min(self.limit_max_mm, mm))

    def in_limits(self, mm: float) -> bool:
        return self.limit_min_mm <= mm <= self.limit_max_mm


# Fine/coarse jog steps chosen per arm. The delay steps are ~20x finer than the grating's
# because ~0.05 mm of delay tracks ~10 mm of grating travel (XCORR_SPEC.md sec. 4.3).
ARM_SPECS: dict[Arm, ArmSpec] = {
    Arm.GRATING: ArmSpec(
        arm=Arm.GRATING, label="Grating", stage="UTS150CC", axis=3,
        limit_min_mm=-75.0, limit_max_mm=75.0,
        step_fine_mm=0.05, step_coarse_mm=1.0,
    ),
    Arm.DELAY: ArmSpec(
        arm=Arm.DELAY, label="Delay", stage="MFA-CC", axis=2,
        limit_min_mm=0.0, limit_max_mm=25.0,
        step_fine_mm=0.005, step_coarse_mm=0.1,
    ),
    Arm.PROBE: ArmSpec(
        arm=Arm.PROBE, label="Probe", stage="FMS300PP", axis=1,
        limit_min_mm=-9.5, limit_max_mm=290.5,
        step_fine_mm=0.1, step_coarse_mm=2.0,
    ),
}
