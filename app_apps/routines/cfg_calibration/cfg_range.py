from __future__ import annotations

import math
from dataclasses import dataclass

from base_core.quantities.constants import SPEED_OF_LIGHT
from base_core.quantities.models import Frequency, Time

from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    StabilizationConfig,
)


@dataclass
class CfgRange:
    """CFG polarization rotation frequency range at the temporal FWHM edges.

    min/max are the rotation frequencies at t = ±T_fwhm/2, derived by gauging the
    spectral axis to the time axis via the FWHM tie point: t(Ω) = Ω × T_fwhm / Δω_fwhm.
    """

    min: Frequency
    max: Frequency
    fwhm: Time

    @classmethod
    def from_stabilization_config(cls, config: StabilizationConfig, fwhm: Time) -> CfgRange:
        """
        NEEDS TO BE IMPLEMENTED
        """
        

        return cls(
            min=Frequency(0),
            max=Frequency(0),
            fwhm=fwhm,
        )
