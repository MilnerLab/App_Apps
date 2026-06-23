from __future__ import annotations

from base_core.framework.app.context import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from base_core.quantities.models import Frequency, Time

from app_apps.analysis.phase_control.module import PhaseControlModule
from app_apps.analysis.phase_control.phase_tracking_handle import PhaseTrackingHandle
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.routines.cfg_calibration.cfg_range import CfgRange


class RoutinesModule(BaseModule):
    name = "routines"
    requires = (PhaseControlModule,)

    def register(self, c: Container, ctx: AppContext) -> None:
        c.register_singleton(CfgRange, lambda _: CfgRange(
            min=Frequency(0.0),
            max=Frequency(0.0),
            fwhm=Time(100e-15),
        ))

        from base_qt.app.dispatcher import QtDispatcher
        from app_apps.routines.cfg_calibration.ui.vm import CfgCalibrationVM

        c.register_factory(CfgCalibrationVM, lambda c: CfgCalibrationVM(
            bus=ctx.event_bus,
            dispatcher=c.get(QtDispatcher),
            handle=c.get(PhaseTrackingHandle),
            config=c.get(StabilizationConfig),
            cfg_range=c.get(CfgRange),
        ))
