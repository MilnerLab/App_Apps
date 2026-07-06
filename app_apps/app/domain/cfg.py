from __future__ import annotations

import math
from dataclasses import dataclass

from base_core.math.models import Range
from base_core.quantities.constants import SPEED_OF_LIGHT
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Frequency, Time
from base_core.quantities.specific_models import AngularChirp
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    StabilizationConfig,
)

# Sign of the reference GDD phi''_0 (Eq. 11 only fixes its magnitude via a
# sqrt); fixed by which direction the stretcher/compressor chirps the pulse,
# calibrated once experimentally — mirrors CORRECTION_SIGN in phase_corrector.py.
GDD_SIGN = 1


@dataclass
class CFG:
    frequencies_fwhm: Range[Frequency]
    T_0_fwhm: Time                    # xcorr-measured duration of the actual chirped pulse (given, fixed)
    beta_0: AngularChirp               # reference (R) arm chirp, rad/ns^2
    beta_grating_arm: AngularChirp     # second (L / grating) arm chirp, rad/ns^2

    def update(self, config: StabilizationConfig) -> None:
        lambda0_nm = config.params.lambda0.value(Prefix.NANO)
        delta_lambda_nm = config.params.delta_lambda_fwhm.value(Prefix.NANO)

        # domega_fwhm: same expression as spectrum_fit's domega_fwhm (base_core/math/functions.py)
        domega_fwhm = 2.0 * math.pi * SPEED_OF_LIGHT / lambda0_nm**2 * delta_lambda_nm

        t_tl_fwhm_ns = 4.0 * math.log(2.0) / domega_fwhm            # Eq. (10) at zero GDD: transform-limited duration
        a = 4.0 * math.log(2.0) / domega_fwhm**2                     # Eq. (6)

        t_0_ns = self.T_0_fwhm.value(Prefix.NANO)                    # xcorr-measured, given
        phi_0 = GDD_SIGN * math.sqrt(max(t_0_ns**2 - t_tl_fwhm_ns**2, 0.0)) / domega_fwhm  # Eq. (11)

        beta_r = -phi_0 / (a**2 + phi_0**2)                           # Eq. (9)
        self.beta_0 = AngularChirp(beta_r, time_prefix=Prefix.NANO)

        delta_phi = 2.0 * float(config.params.theta2)     # Eq. (5): Theta2 = 1/2 * Delta phi''
        phi_grating = phi_0 - delta_phi                     # phi''_grating_arm = phi''_0 - Delta phi''
        beta_l = -phi_grating / (a**2 + phi_grating**2)      # Eq. (9)
        self.beta_grating_arm = AngularChirp(beta_l, time_prefix=Prefix.NANO)

        tau_ns = -float(config.params.theta1)               # Eq. (5): Theta1 = -tau

        # Eq. (13), with z_R = 0 (reference arm) so u_R(t) = t and u_L(t) = t + tau:
        #   2*pi*f_cfg(t) = 1/2 * [beta_R * t - beta_L * (t + tau)]
        # evaluated at the edges of the actual (measured) pulse duration, t = +/- T_0_fwhm/2.
        half_span_ns = t_0_ns / 2.0
        f_minus = 0.5 * (beta_r * (-half_span_ns) - beta_l * (-half_span_ns + tau_ns)) / (2.0 * math.pi)
        f_plus = 0.5 * (beta_r * (+half_span_ns) - beta_l * (+half_span_ns + tau_ns)) / (2.0 * math.pi)
        f_lo, f_hi = (f_minus, f_plus) if f_minus <= f_plus else (f_plus, f_minus)
        self.frequencies_fwhm = Range(Frequency(f_lo, Prefix.GIGA), Frequency(f_hi, Prefix.GIGA))
