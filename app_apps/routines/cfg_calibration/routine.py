from __future__ import annotations

import dataclasses

from app_apps.routines.linear.lab import Lab
from app_apps.routines.linear.registry import routine


@routine("cfg_calibration")
def cfg_calibration(lab: Lab) -> None:
    """Calibrate CFG spectral fit with all parameters free.

    Enables full-parameter fitting at the start so the stabilization worker refines
    the envelope and phase model together, then restores phase-only mode in the finally
    block — even if cancelled.
    """
    handle = lab.phase_tracking
    config_on = dataclasses.replace(handle.config, fit_all_params=True)
    handle.set_config(config_on)
    try:
        pass  # TODO: await convergence, record CfgRange
    finally:
        config_off = dataclasses.replace(handle.config, fit_all_params=False)
        handle.set_config(config_off)
