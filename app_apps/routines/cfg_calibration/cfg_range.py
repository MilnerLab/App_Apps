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
        """Compute CFG rotation frequency at the ±FWHM spectral edges.

        Uses Θ = dphi0 − tau·Ω + ½·delta_beta·Ω² from cfg_spectrum; the CFG
        rotation frequency at detuning Ω is (dΘ/dΩ) × (Δω_fwhm / T_fwhm) / (4π).
        """
        lambda0_m = float(config.lambda0)           # m (Length is SI float subclass)
        delta_lambda_m = float(config.delta_lambda_fwhm)   # m
        tau = config.delta_z * 1e-3 / SPEED_OF_LIGHT       # s  (delta_z in mm)
        delta_beta = config.delta_beta * 1e-24              # s² (delta_beta in ps²)
        t_fwhm = float(fwhm)                               # s

        domega = 2 * math.pi * SPEED_OF_LIGHT * delta_lambda_m / lambda0_m ** 2  # rad/s
        scale = domega / (4 * math.pi * t_fwhm)   # converts dΘ/dΩ [s] → ν [Hz]
        nu_lo = (-tau - delta_beta * domega / 2) * scale
        nu_hi = (-tau + delta_beta * domega / 2) * scale

        return cls(
            min=Frequency(min(nu_lo, nu_hi)),
            max=Frequency(max(nu_lo, nu_hi)),
            fwhm=fwhm,
        )
