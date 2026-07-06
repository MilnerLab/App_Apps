from __future__ import annotations

import math
from dataclasses import dataclass

from base_core.math.models import Range
from base_core.quantities.constants import SPEED_OF_LIGHT
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Frequency, Time
from base_core.quantities.specific_models import AngularChirp
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    SpectralFitParams,
)

# Sign of the reference GDD phi''_0 (Eq. 11 only fixes its magnitude via a
# sqrt); fixed by which direction the stretcher/compressor chirps the pulse,
# calibrated once experimentally — mirrors CORRECTION_SIGN in phase_corrector.py.
GDD_SIGN = 1


@dataclass
class CFG:
    T_DA_fwhm: Time = None                 # xcorr-measured duration of the actual chirped pulse (given, fixed)
    
    frequencies_fwhm: Range[Frequency] = None
    
    T_GA_fwhm: Time = None
    beta_DA: AngularChirp = None              # reference (R) arm chirp, rad/ps^2
    beta_GA: AngularChirp = None    # second (L / grating) arm chirp, rad/ps^2

    def update(self, params: SpectralFitParams) -> None:
        if self.T_DA_fwhm is None:
            raise Exception("Should not be null: T_DA")
        
        lambda0_nm = params.lambda0.value(Prefix.NANO)
        delta_lambda_nm = params.delta_lambda_fwhm.value(Prefix.NANO)

        # domega_fwhm: same expression as spectrum_fit's domega_fwhm (base_core/math/functions.py),
        # including its 1e-3 factor putting this in rad/ps (matching theta1/theta2's ps/ps^2 units)
        domega_fwhm = 2.0 * math.pi * SPEED_OF_LIGHT / lambda0_nm**2 * delta_lambda_nm * 1e-3

        ln2 = math.log(2.0)
        t_tl_fwhm_ps = 4.0 * ln2 / domega_fwhm                        # Eq. (10) at zero GDD: transform-limited duration
        a = 4.0 * ln2 / domega_fwhm**2                                # Eq. (6)

        # --- DA (reference, R) arm: chirp from the xcorr-measured T_DA_fwhm alone ---
        t_da_ps = self.T_DA_fwhm.value(Prefix.PICO)                   # xcorr-measured, given
        phi_da = GDD_SIGN * math.sqrt(max(t_da_ps**2 - t_tl_fwhm_ps**2, 0.0)) / domega_fwhm  # Eq. (11): phi''_DA
        beta_da = -phi_da / (a**2 + phi_da**2)                        # Eq. (9)
        self.beta_DA = AngularChirp(beta_da, time_prefix=Prefix.PICO)

        # --- GA (grating, L) arm: offset from DA by the fitted differential GDD ---
        delta_phi = 2.0 * params.theta2.value(Prefix.PICO)  # Eq. (5): Theta2 = 1/2 * Delta phi'' = 1/2 (phi''_DA - phi''_GA)
        phi_ga = phi_da - delta_phi                      # phi''_GA = phi''_DA - Delta phi''
        beta_ga = -phi_ga / (a**2 + phi_ga**2)           # Eq. (9)
        self.beta_GA = AngularChirp(beta_ga, time_prefix=Prefix.PICO)

        t_ga_fwhm_ps = math.sqrt(t_tl_fwhm_ps**2 + (phi_ga * domega_fwhm)**2)  # Eq. (10): T_GA,FWHM from phi''_GA
        self.T_GA_fwhm = Time(t_ga_fwhm_ps, Prefix.PICO)

        tau_ps = -params.theta1.value(Prefix.PICO)         # Eq. (5): Theta1 = -tau

        # --- FWHM frequency range of the centrifuge: overlap envelope of both arms ---
        # Gaussian field widths T_R, T_L from the measured intensity FWHMs (relation used
        # right before Eq. (10): T_j,FWHM^2 = 4*ln2*T_j^2)
        t_r_ps = t_da_ps / (2.0 * math.sqrt(ln2))
        t_l_ps = t_ga_fwhm_ps / (2.0 * math.sqrt(ln2))

        t_eff_ps = t_r_ps * t_l_ps / math.sqrt(t_r_ps**2 + t_l_ps**2)  # Eq. (17)

        # center frequency at the overlap envelope center t_c, with z_R = 0            # Eq. (20)
        f_center = -tau_ps * (beta_da * t_r_ps**2 + beta_ga * t_l_ps**2) / (4.0 * math.pi * (t_r_ps**2 + t_l_ps**2))
        half_width = (beta_da - beta_ga) / (4.0 * math.pi) * math.sqrt(2.0 * ln2) * t_eff_ps  # Eq. (19)

        f_minus, f_plus = f_center - half_width, f_center + half_width
        f_lo, f_hi = (f_minus, f_plus) if f_minus <= f_plus else (f_plus, f_minus)
        # NOTE: the ps-domain rescaling above makes the bare f_center/half_width numbers
        # 1000x smaller than under the old ns-domain convention, so this must be TERA
        # (not GIGA) to reconstruct the same true frequency — verified numerically.
        self.frequencies_fwhm = Range(Frequency(f_lo, Prefix.TERA), Frequency(f_hi, Prefix.TERA))
