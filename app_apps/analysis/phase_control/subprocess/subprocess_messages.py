from __future__ import annotations

from dataclasses import dataclass

from base_core.framework.subprocess.messages import Message, Kind
from app_apps.analysis.phase_control.domain.config import AnalysisConfig
from app_apps.analysis.phase_control.domain.envelope_config import EnvelopeConfig


@dataclass(frozen=True)
class CorrectionAvailable(Message):
    NAME = "CorrectionAvailable"
    KIND = Kind.EVENT

    correction_deg: float
    phase_deg: float
    sign: int


@dataclass(frozen=True)
class ConfigSynced(Message):
    NAME = "ConfigSynced"
    KIND = Kind.EVENT

    config: AnalysisConfig


@dataclass(frozen=True)
class SetAnalysisConfig(Message):
    NAME = "SetAnalysisConfig"
    KIND = Kind.COMMAND

    config: AnalysisConfig


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
