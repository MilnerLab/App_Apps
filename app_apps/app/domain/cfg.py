from __future__ import annotations

import math
from dataclasses import dataclass

from base_core.math.models import Range
from base_core.quantities.constants import SPEED_OF_LIGHT
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Frequency, Time
from base_core.quantities.specific_models import AngularChirp
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    FringeFitParams,
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

    def update(self, params: FringeFitParams) -> None:
        # TODO: not ported to the cubic-phase fit. The previous derivation of
        # beta_DA/beta_GA/T_GA_fwhm/frequencies_fwhm consumed the old skew model's
        # Omega-domain theta1 (=-tau, ps) and theta2 (=GDD, ps^2). FringeFitParams
        # instead carries a wavelength-domain cubic (c1/c2/c3 in nll powers of
        # lambda-l0); the mapping to tau/GDD must be re-derived before this can be
        # revived. This method is currently unused (no live caller). See git
        # history (pre cubic-fit swap) for the original implementation.
        raise NotImplementedError(
            "CFG.update is not implemented for the cubic-phase fit; re-derive "
            "tau/GDD from FringeFitParams.c1/c2/c3 before use."
        )
