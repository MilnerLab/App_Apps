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
class SetStabilizationConfig(Request[OKReply]):
    config: StabilizationConfig = None  # type: ignore[assignment]


@register
@dataclass(frozen=True)
class SetEnvelopeConfig(Request[OKReply]):
    config: EnvelopeConfig = None  # type: ignore[assignment]


# --- block-averaged correction loop ------------------------------------------------------
@register
@dataclass(frozen=True)
class CaptureTarget(Request[OKReply]):
    """Main -> subprocess: adopt the measured phase as the new target.

    Collect one fresh block of accepted fits and write its circular mean into
    ``config.set_phase``, so the loop holds the fringes where they are NOW. This is what
    re-references the loop after the centrifuge changes: the per-shot fit already refits the
    envelope and chirp on every frame, so the shape needs no re-capture -- only the setpoint
    does, and there is no way to know the new one but to measure it.
    """


@register
@dataclass(frozen=True)
class DropBatch(Message):
    """Main -> subprocess: a commanded delay or grating move changed the fringes.

    Discards the partially filled block. Fire-and-forget, and deliberately so: it must land
    before the disturbed spectra do, and a request/reply round trip would put the reply on
    the critical path of a stage move.

    The probe stage (FMS300PP) is deliberately not a source of this: it does not change the
    fringe shape.
    """
    reason: str = ""


@register
@dataclass(frozen=True)
class BatchProgress(Message):
    """Subprocess -> main: how the current averaging block is filling.

    ``coherence`` is |z| of the collected phases: 1 means every fit in the block agreed, and
    near 0 means they cancelled and the mean would be meaningless. Nothing gates on it -- it
    is there so a block that is averaging noise can be told from one that is simply quiet,
    which the count alone cannot show.

    ``capturing`` marks a block being collected for Capture target rather than for a
    correction, so the panel can say which of the two the operator is waiting on.

    ``error_deg`` is the block's running circular mean measured against the setpoint, folded
    the same way the corrector folds it, in degrees. It is what the loop WOULD correct if the
    block filled right now, so reading it against the deadband says whether the next
    correction will move the plate at all. NaN while the block is empty -- there is no error
    to report before a single phase has landed, and 0.0 would read as a perfect lock.
    """
    collected: int = 0
    needed: int = 0
    coherence: float = 0.0
    capturing: bool = False
    settling: bool = False
    error_deg: float = float("nan")
