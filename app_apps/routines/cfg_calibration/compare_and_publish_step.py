from __future__ import annotations

import logging

from base_core.framework.routines.step import Step

from app_apps.routines.cfg_calibration.cfg_range import CfgRange

log = logging.getLogger(__name__)


class CompareAndPublishStep(Step):
    """Compare computed CfgRange to target and publish translation stage events.

    Stage connection not yet available; logs computed vs. configured range for now.
    """

    def __init__(self, slot: int, cfg_range: CfgRange) -> None:
        super().__init__(slot)
        self._cfg_range = cfg_range

    def start(self) -> None:
        log.info(
            "CFG calibration step 2 — computed range: min=%.3e Hz, max=%.3e Hz (fwhm=%.3e s)",
            float(self._cfg_range.min),
            float(self._cfg_range.max),
            float(self._cfg_range.fwhm),
        )

    def stop(self) -> None:
        pass

    def reset(self) -> None:
        pass
