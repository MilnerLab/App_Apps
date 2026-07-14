from __future__ import annotations

from dataclasses import dataclass

from base_core.ipc.codec import register
from base_core.ipc.message import Message, Request, OKReply
from base_core.math.models import Angle
from app_apps.analysis.phase_control.subprocess.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig


@register
@dataclass(frozen=True)
class ProcessSpectrum(Message):
    """Main → subprocess: a spectrum slot is ready for processing."""
    slot: int = 0
    item_id: int = 0
    timestamp_ns: int = 0


@register
@dataclass(frozen=True)
class SpectrumProcessed(Message):
    """Subprocess → main: worker finished reading a spectrum slot."""
    slot: int = 0
    item_id: int = 0
    consumer_id: str = ""


@register
@dataclass(frozen=True)
class CorrectionAvailable(Message):
    angle: Angle = None  # type: ignore[assignment]
    sign: int = 0


@register
@dataclass(frozen=True)
class ConfigSynced(Message):
    config: StabilizationConfig = None  # type: ignore[assignment]


@register
@dataclass(frozen=True)
class FitCurveAvailable(Message):
    """Subprocess → main: components of the latest fringe-fit model, for the chart overlay.

    The model at the tracked phase is  baseline + amplitude*cos(phase).  Shift `phase` by
    (set_phase - phase_ref) to draw the same fit at the target phase.  Sent per good fit.
    """
    wavelengths_nm: list = None  # type: ignore[assignment]  # list[float]
    baseline: list = None        # type: ignore[assignment]  # list[float]
    amplitude: list = None       # type: ignore[assignment]  # list[float]
    phase: list = None           # type: ignore[assignment]  # list[float]
    phase_ref_rad: float = 0.0


@register
@dataclass(frozen=True)
class SetStabilizationConfig(Request[OKReply]):
    config: StabilizationConfig = None  # type: ignore[assignment]


@register
@dataclass(frozen=True)
class SetEnvelopeConfig(Request[OKReply]):
    config: EnvelopeConfig = None  # type: ignore[assignment]
