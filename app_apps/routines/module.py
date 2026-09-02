from __future__ import annotations

from base_core.framework.app.context import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from base_core.quantities.models import Frequency, Time

from app_apps.analysis.phase_control.module import PhaseControlModule
from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.io.control_readout.module import ControlReadoutModule
from app_apps.io.control_readout.fms300pp.handler import Fms300ppHandle
from app_apps.io.control_readout.mfa_cc.handler import MfaccHandle
from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle
from app_apps.routines.cfg_auto_calibration.fit import CentrifugeFitMap
from app_apps.routines.cfg_calibration.cfg_range import CfgRange


class RoutinesModule(BaseModule):
    name = "routines"
    requires = (PhaseControlModule, ControlReadoutModule)

    def register(self, c: Container, ctx: AppContext) -> None:
        c.register_singleton(CfgRange, lambda _: CfgRange(
            min=Frequency(0.0),
            max=Frequency(0.0),
            fwhm=Time(100e-15),
        ))

        from base_qt.app.dispatcher import QtDispatcher
        from app_apps.routines.cfg_calibration.ui.view_model import CfgCalibrationViewModel
        from app_apps.routines.cfg_calibration.ui.view import CfgCalibrationView

        c.register_factory(CfgCalibrationViewModel, lambda c: CfgCalibrationViewModel(
            bus=ctx.event_bus,
            dispatcher=c.get(QtDispatcher),
            handle=c.get(PhaseStabilizationHandle),
            config=c.get(StabilizationConfig),
            cfg_range=c.get(CfgRange),
        ))
        c.register_factory(CfgCalibrationView, lambda c: CfgCalibrationView(c.get(CfgCalibrationViewModel), parent=None))

        # --- CFG auto-calibration (operator-driven send-to; distinct from the above) ---
        c.register_singleton(CentrifugeFitMap, lambda _: CentrifugeFitMap())

        from app_apps.routines.cfg_auto_calibration.ui.view_model import CfgAutoCalibrationViewModel
        from app_apps.routines.cfg_auto_calibration.ui.view import CfgAutoCalibrationView

        c.register_factory(CfgAutoCalibrationViewModel, lambda c: CfgAutoCalibrationViewModel(
            bus=ctx.event_bus,
            dispatcher=c.get(QtDispatcher),
            grating=c.get(Uts150ccHandle),
            delay=c.get(MfaccHandle),
            probe=c.get(Fms300ppHandle),
            fit_map=c.get(CentrifugeFitMap),
        ))
        c.register_factory(CfgAutoCalibrationView, lambda c: CfgAutoCalibrationView(c.get(CfgAutoCalibrationViewModel), parent=None))
