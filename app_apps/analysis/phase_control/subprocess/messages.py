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
class CorrectionStatus(Message):
    """Subprocess -> main: the loop reached a correction instant, and what it did there.

    Sent whether or not a correction went out, because "nothing was commanded" is the
    interesting case: it is how the panel tells a loop sitting inside the deadband apart
    from one that is not running at all. ``period_s`` is what the countdown to the next
    instant is measured against.
    """
    phase_error_rad: float = 0.0
    commanded_deg: float = 0.0
    applied: bool = False
    reason: str = ""          # empty when applied; else why nothing was commanded
    period_s: float = 0.0
    frames: int = 0           # frames averaged into this instant


@register
@dataclass(frozen=True)
class RunningPhaseMean(Message):
    """Subprocess -> main: the circular running mean the NEXT correction will be made from.

    Not the same thing as CorrectionStatus, which is sent once per correction instant and
    describes what was done. This is the loop's current opinion, sent while it accumulates,
    so the panel can draw the averaged trace the correction will actually act on rather
    than only the raw frame and the reference it is being compared to.

    ``coherence`` is |z| of the unit-vector EWMA: 1 means every frame in the window agreed,
    and near 0 means they cancelled and the mean angle means nothing.
    """
    mean_phase_rad: float = 0.0
    frames: int = 0
    coherence: float = 0.0


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
    """Main → subprocess: install a template read from a file, overriding the current one.

    A recalled template is PINNED: nothing automatic replaces it. ``template=None`` is the
    operator deselecting the recall, which is the only way back to automatic capture short
    of pressing Capture reference.
    """
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
    pinned: bool = False   # the template was recalled off disk and is held until deselected
    abandoned: int = 0     # completed runs of 10 the averaged-trace gates rejected; the
                           # capture is still retrying
