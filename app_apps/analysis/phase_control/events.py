from __future__ import annotations

from dataclasses import dataclass

from app_apps.analysis.phase_control.subprocess.domain.phase_template import PhaseTemplate


@dataclass
class StabilizationConfigChanged:
    pass


@dataclass
class PhaseTrackingStateChanged:
    pass


@dataclass
class EnvelopeStateChanged:
    pass


@dataclass
class PhaseTemplateChanged:
    """The frozen-template state machine moved. ``template`` is set only in "locked"."""
    state: str = "off"
    captured: int = 0
    needed: int = 0
    template: PhaseTemplate | None = None
