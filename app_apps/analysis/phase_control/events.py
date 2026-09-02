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
class PhaseCorrectionReported:
    """One correction instant: what the error was, and what was (or was not) commanded.

    Published on every instant, including the ones where nothing was commanded -- that is
    the case the panel most needs to show, since a loop inside the deadband looks exactly
    like a stopped one otherwise.
    """
    phase_error_rad: float = 0.0
    commanded_deg: float = 0.0
    applied: bool = False
    reason: str = ""
    period_s: float = 0.0
    frames: int = 0


@dataclass
class PhaseMeanReported:
    """The circular running mean of the frames accumulated so far, i.e. what the next
    correction will be computed from. ``valid`` is False once the loop has flushed the
    average and has nothing to say yet."""
    mean_phase_rad: float = 0.0
    frames: int = 0
    coherence: float = 0.0
    valid: bool = False


@dataclass
class PhaseTemplateChanged:
    """The frozen-template state machine moved. ``template`` is set only in "locked"."""
    state: str = "off"
    captured: int = 0
    needed: int = 0
    template: PhaseTemplate | None = None
    pinned: bool = False
    abandoned: int = 0
