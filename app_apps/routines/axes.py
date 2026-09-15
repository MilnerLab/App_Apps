"""Which stage plays which part in this rig.

The split against the Devices repo is deliberate and worth keeping straight:

* A :class:`~control_readout.base.stage_spec.StageSpec` says what is true about a *stage*
  — its model, the units it counts in, how far it may travel. Those facts belong to the
  hardware and live in the stage's own package under ``control_readout/esp_301/``.
* An :class:`AxisRole` says what that stage is *for* — that the UTS150CC is the grating
  arm, that we call it "Grating" in the UI, and how far one press of the jog button
  should move it. Those facts belong to this experiment and change when the rig does.

Jog steps are the clearest example of the distinction. The delay steps are ~20x finer
than the grating's not because an MFA-CC is a finer stage than a UTS150CC, but because
~0.05 mm of delay tracks ~10 mm of grating travel in this optical layout
(XCORR_SPEC.md sec. 4.3). That is rig physics, so it lives here.

Three ESP301 axes, all on one serial port behind a single lock, so moves are serialized:

    axis 1  FMS300PP  PROBE     (xcorr scan axis)
    axis 2  MFA-CC    DELAY     (sets the central frequency)
    axis 3  UTS150CC  GRATING   (sets the swept bandwidth)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from control_readout.base.stage_spec import StageSpec
from control_readout.esp_301.fms300pp import spec as fms300pp_spec
from control_readout.esp_301.mfa_cc import spec as mfa_cc_spec
from control_readout.esp_301.uts150cc import spec as uts150cc_spec


class Axis(Enum):
    """A role in the rig. The values double as the role keys used across XCORR."""

    GRATING = "grating"
    DELAY = "delay"
    PROBE = "probe"


@dataclass(frozen=True)
class AxisRole:
    """One arm of the rig: a stage, the name we call it, and how it is jogged."""

    axis: Axis
    #: UI label.
    label: str
    #: The stage filling this role. Limits, units and model come from here — never
    #: restated below, so there is exactly one place to correct when a limit changes.
    spec: StageSpec
    #: 1-based ESP301 axis, from the stage's package.
    esp_axis: int
    #: The ``<`` / ``>`` jog, mm. Rig-tuned, not a stage property.
    step_fine_mm: float
    #: The ``<<`` / ``>>`` jog, mm.
    step_coarse_mm: float

    # -- limit helpers, delegated to the spec so behaviour cannot drift --------

    @property
    def limit_min_mm(self) -> float:
        return self.spec.limit_min

    @property
    def limit_max_mm(self) -> float:
        return self.spec.limit_max

    @property
    def stage(self) -> str:
        """Device model, for provenance and tooltips."""
        return self.spec.model

    def clamp(self, mm: float) -> float:
        return self.spec.clamp(mm)

    def in_limits(self, mm: float) -> bool:
        return self.spec.in_limits(mm)


AXIS_ROLES: dict[Axis, AxisRole] = {
    Axis.GRATING: AxisRole(
        axis=Axis.GRATING, label="Grating",
        spec=uts150cc_spec.SPEC, esp_axis=uts150cc_spec.AXIS,
        step_fine_mm=0.05, step_coarse_mm=1.0,
    ),
    Axis.DELAY: AxisRole(
        axis=Axis.DELAY, label="Delay",
        spec=mfa_cc_spec.SPEC, esp_axis=mfa_cc_spec.AXIS,
        step_fine_mm=0.005, step_coarse_mm=0.1,
    ),
    Axis.PROBE: AxisRole(
        axis=Axis.PROBE, label="Probe",
        spec=fms300pp_spec.SPEC, esp_axis=fms300pp_spec.AXIS,
        step_fine_mm=0.1, step_coarse_mm=2.0,
    ),
}

#: Per-role soft limits, keyed by the role's string value. The shape XCORR's planner and
#: provenance want; derived so it can never disagree with the stage packages.
AXIS_LIMITS: dict[str, tuple[float, float]] = {
    role.axis.value: role.spec.limits for role in AXIS_ROLES.values()
}
