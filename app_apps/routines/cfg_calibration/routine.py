"""``CfgCalibrationRoutine`` — resync the stabilization config, then derive a CfgRange.

Two phases, written as two statements of one dispatched method rather than as two ``Step``
objects. The step machinery this used to sit on could not express either of them honestly:
phase one is *wait for an event*, which a ``start()`` that must return immediately can only
do by subscribing and calling back into the routine's own ``advance_step`` — the state
driving its own transition — and phase two had no way to report that it failed. Both read
as ordinary code here, and the wait is a plain ``Event.wait`` on the routine's thread.
"""
from __future__ import annotations

import logging
import threading

from base_core.framework.events import EventBus
from base_core.framework.routines.routine_base import BaseRoutine, routine_thread
from base_core.ipc.worker_handle import WorkerStatus

from app_apps.analysis.phase_control.events import StabilizationConfigChanged
from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.routines.cfg_calibration.cfg_range import CfgRange

log = logging.getLogger(__name__)

#: How long to wait for the subprocess to acknowledge the config with a
#: ``StabilizationConfigChanged``. Generous: the worker may be mid-block when asked.
_CONFIG_TIMEOUT_S = 30.0


class CfgCalibrationRoutine(BaseRoutine):

    def __init__(
        self,
        bus: EventBus,
        handle: PhaseStabilizationHandle,
        config: StabilizationConfig,
        cfg_range: CfgRange,
    ) -> None:
        self._handle = handle
        self._config = config
        self._cfg_range = cfg_range
        #: Set by the bus handler (publisher's thread) and waited on by the routine
        #: thread — the same discipline as run control, for the same reason.
        self._config_seen = threading.Event()
        super().__init__(bus)
        self._control.begin()
        self._run()

    def _setup(self) -> None:
        self._unsubs.append(
            self._bus.subscribe(StabilizationConfigChanged, self._on_config_changed)
        )

    def _on_config_changed(self, _: StabilizationConfigChanged) -> None:
        self._config_seen.set()

    @routine_thread
    def _run(self) -> None:
        # No failure event: nothing outside this routine waits on the calibration, so a
        # logged traceback is the whole of what a failure needs to produce here.
        with self.run_lifecycle():
            # The cubic-phase fit has no full/phase-only mode toggle (every shot is a
            # full fit), so calibration just resyncs the config to the subprocess.
            if self._handle.state == WorkerStatus.RUNNING:
                self._handle.set_config(self._config)

            if not self._config_seen.wait(_CONFIG_TIMEOUT_S):
                log.warning(
                    "CFG calibration: no StabilizationConfigChanged within %.0fs — "
                    "computing the range from the config as it stands",
                    _CONFIG_TIMEOUT_S,
                )

            computed = CfgRange.from_stabilization_config(self._config, self._cfg_range.fwhm)
            self._cfg_range.min = computed.min
            self._cfg_range.max = computed.max

            log.info(
                "CFG calibration — computed range: min=%.3e Hz, max=%.3e Hz (fwhm=%.3e s)",
                float(self._cfg_range.min),
                float(self._cfg_range.max),
                float(self._cfg_range.fwhm),
            )

    def dispose(self) -> None:
        if self._handle.state == WorkerStatus.RUNNING:
            self._handle.set_config(self._config)
        # Free the routine thread if it is still parked on the config wait, so disposal
        # does not block behind a timeout that no longer has anyone to report to.
        self._config_seen.set()
        self._control.abort()
        super().dispose()
