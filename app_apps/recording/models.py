"""What a spectrometer recording writes besides the spectra themselves.

Both are ``PrimitiveSerde`` dataclasses and are stored as JSON through
``h5_utils.write_primitive``, so a reader rebuilds them with ``read_primitive`` -- or, in
a program without these classes, ``json.loads`` the string.

``Optional[...]`` rather than ``X | None``: ``_convert_field`` recognises ``typing.Union``
only, and a PEP 604 union would come back as a bare float instead of a ``Length``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from base_core.framework.serialization.serde import PrimitiveSerde
from base_core.quantities.models import Length
from spm_002.config import SpectrometerConfig

from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    StabilizationConfig,
)

FORMAT_NAME = "milnerlab-spectrum"
FORMAT_VERSION = 1


@dataclass
class SpectrumRecordingMetadata(PrimitiveSerde):
    """The rig state in force from this entry's timestamp until the next entry.

    A trace belongs to the latest entry whose timestamp is not after its own.
    """

    #: What caused this entry: "start", "grating", "delay", "stabilization",
    #: "stabilization_config", "spectrometer_config".
    reason: str
    #: ``None`` until the stage has reported a position.
    grating_position: Optional[Length]
    delay_position: Optional[Length]
    stabilization_running: bool
    spectrometer: SpectrometerConfig
    #: Includes ``set_phase``, the phase the loop stabilizes to.
    stabilization: StabilizationConfig


@dataclass
class SpectrumRecordingInfo(PrimitiveSerde):
    """Written at start and rewritten at stop, so a file from a killed process still
    says what it is -- with ``stopped_utc`` left ``None``."""

    format_name: str
    format_version: int
    source: str
    started_utc: str
    stopped_utc: Optional[str] = None
    n_traces: int = 0
    n_dropped: int = 0
