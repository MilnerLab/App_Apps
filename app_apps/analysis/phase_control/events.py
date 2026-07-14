from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StabilizationConfigChanged:
    pass


@dataclass
class FitCurvesChanged:
    """Latest fringe-fit model components (main-process event for the chart overlay).

    Reconstruct the fit as baseline + amplitude*cos(phase); the set-phase curve shifts
    `phase` by (set_phase - phase_ref_rad). Empty lists mean "no fit yet".
    """
    wavelengths_nm: list[float] = field(default_factory=list)
    baseline: list[float] = field(default_factory=list)
    amplitude: list[float] = field(default_factory=list)
    phase: list[float] = field(default_factory=list)
    phase_ref_rad: float = 0.0


@dataclass
class PhaseTrackingStateChanged:
    pass


@dataclass
class EnvelopeStateChanged:
    pass
