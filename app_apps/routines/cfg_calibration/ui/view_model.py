from __future__ import annotations

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_view_model import PanelViewModel

from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.routines.cfg_calibration.cfg_range import CfgRange
from app_apps.routines.cfg_calibration.routine import CfgCalibrationRoutine


class CfgCalibrationViewModel(PanelViewModel):

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: PhaseStabilizationHandle,
        config: StabilizationConfig,
        cfg_range: CfgRange,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._handle = handle
        self._config = config
        self._cfg_range = cfg_range
        self._routine: CfgCalibrationRoutine | None = None

    @property
    def cfg_range(self) -> CfgRange:
        return self._cfg_range

    def start(self) -> None:
        if self._routine is not None:
            self._routine.stop()
        self._routine = CfgCalibrationRoutine(
            bus=self._bus,
            handle=self._handle,
            config=self._config,
            cfg_range=self._cfg_range,
        )
