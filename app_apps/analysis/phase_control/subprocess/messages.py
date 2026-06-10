from __future__ import annotations

from dataclasses import dataclass

from base_core.framework.subprocess.messages import Message, Kind
from base_core.math.models import Angle
from app_apps.analysis.phase_control.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.domain.envelope_config import EnvelopeConfig


@dataclass(frozen=True)
class CorrectionAvailable(Message):
    NAME = "CorrectionAvailable"
    KIND = Kind.EVENT

    angle: Angle
    sign: int


@dataclass(frozen=True)
class ConfigSynced(Message):
    NAME = "ConfigSynced"
    KIND = Kind.EVENT

    config: StabilizationConfig


@dataclass(frozen=True)
class SetStabilizationConfig(Message):
    NAME = "SetStabilizationConfig"
    KIND = Kind.COMMAND

    config: StabilizationConfig


@dataclass(frozen=True)
class SetEnvelopeConfig(Message):
    NAME = "SetEnvelopeConfig"
    KIND = Kind.COMMAND

    config: EnvelopeConfig


@dataclass(frozen=True)
class SetPaused(Message):
    NAME = "SetPaused"
    KIND = Kind.COMMAND

    paused: bool


@dataclass(frozen=True)
class Reset(Message):
    NAME = "Reset"
    KIND = Kind.COMMAND
