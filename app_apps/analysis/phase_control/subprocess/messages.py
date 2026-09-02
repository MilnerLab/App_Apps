from __future__ import annotations

from dataclasses import dataclass

from base_core.ipc.codec import register
from base_core.ipc.message import Message, Request, OKReply
from base_core.math.models import Angle
from app_apps.analysis.phase_control.subprocess.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_template import PhaseTemplate


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
class SetStabilizationConfig(Request[OKReply]):
    config: StabilizationConfig = None  # type: ignore[assignment]


@register
@dataclass(frozen=True)
class SetEnvelopeConfig(Request[OKReply]):
    config: EnvelopeConfig = None  # type: ignore[assignment]


# --- frozen-template tracking ------------------------------------------------------------
@register
@dataclass(frozen=True)
class CaptureReference(Request[OKReply]):
    """Main → subprocess: collect the next N accepted traces and install the template."""


@register
@dataclass(frozen=True)
class RecallReference(Request[OKReply]):
    """Main → subprocess: install a template read from a file, overriding the current one."""
    template: PhaseTemplate = None  # type: ignore[assignment]


@register
@dataclass(frozen=True)
class InvalidateTemplate(Message):
    """Main → subprocess: a commanded delay or grating move changed the fringe shape.

    Fire-and-forget, and deliberately so: it must land before the corrupted spectra do, and
    a request/reply round trip would put the reply on the critical path of a stage move.
    A probe move does NOT send this -- it does not change the shape.
    """
    reason: str = ""


@register
@dataclass(frozen=True)
class TemplateStateChanged(Message):
    """Subprocess → main: the template state machine moved, or a template was installed.

    ``template`` is None in every state but LOCKED. It carries the whole template because the
    main side is where Save reference writes it out.
    """
    state: str = "off"
    captured: int = 0
    needed: int = 0
    template: PhaseTemplate = None  # type: ignore[assignment]
