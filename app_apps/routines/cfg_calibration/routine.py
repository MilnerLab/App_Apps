from __future__ import annotations

from base_core.framework.events import EventBus
from base_core.framework.routines.routine_base import BaseRoutine
from base_core.ipc.worker_handle import WorkerStatus

from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.routines.cfg_calibration.cfg_range import CfgRange
from app_apps.routines.cfg_calibration.compare_and_publish_step import CompareAndPublishStep
from app_apps.routines.cfg_calibration.wait_for_config_step import WaitForConfigStep


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
        super().__init__(bus)

        self.add_step(WaitForConfigStep(
            slot=0,
            bus=bus,
            config=config,
            cfg_range=cfg_range,
            on_complete=self.advance_step,
        ))
        self.add_step(CompareAndPublishStep(slot=1, cfg_range=cfg_range))

        self._dispatch(self._start_calibration)

    def _start_calibration(self) -> None:
        # The cubic-phase fit has no full/phase-only mode toggle (every shot is a
        # full fit), so calibration just resyncs the config to the subprocess.
        if self._handle.state == WorkerStatus.RUNNING:
            self._handle.set_config(self._config)
        if self._step_list:
            self._step_list[0].start()

    def stop(self) -> None:
        if self._handle.state == WorkerStatus.RUNNING:
            self._handle.set_config(self._config)
        step = self.current_step
        if step is not None:
            self._dispatch(step.stop)
        super().stop()
