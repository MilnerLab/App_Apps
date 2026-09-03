from __future__ import annotations

from pathlib import Path

from base_core.framework.app.context import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from base_core.quantities.models import Frequency, Time

from app_apps.analysis.phase_control.module import PhaseControlModule
from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.io.control_readout.fms300pp.handler import Fms300ppHandle
from app_apps.io.control_readout.mfa_cc.handler import MfaccHandle
from app_apps.io.control_readout.module import ControlReadoutModule
from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle
from app_apps.io.oscilloscope.module import OscilloscopeModule
from app_apps.io.oscilloscope.oscilloscope_worker_handler import OscilloscopeWorkerHandle
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle
from app_apps.routines.cfg_calibration.cfg_range import CfgRange
from app_apps.routines.spectrum_soak.settings import SoakSettings
from app_apps.routines.xcorr.config import XcorrConfig
from app_apps.routines.xcorr.routine import XcorrRoutine
from app_apps.routines.xcorr.settings import XcorrSettings


class RoutinesModule(BaseModule):
    name = "routines"
    requires = (PhaseControlModule, ControlReadoutModule, OscilloscopeModule)

    def register(self, c: Container, ctx: AppContext) -> None:
        c.register_singleton(CfgRange, lambda _: CfgRange(
            min=Frequency(0.0),
            max=Frequency(0.0),
            fwhm=Time(100e-15),
        ))

        self._register_xcorr(c, ctx)

        from base_qt.app.dispatcher import QtDispatcher
        from app_apps.routines.cfg_calibration.ui.view_model import CfgCalibrationViewModel
        from app_apps.routines.cfg_calibration.ui.view import CfgCalibrationView
        from app_apps.routines.xcorr.ui.view_model import XcorrViewModel
        from app_apps.routines.xcorr.ui.view import XcorrView
        from app_apps.routines.spectrum_soak.ui.view_model import SpectrumSoakViewModel
        from app_apps.routines.spectrum_soak.ui.view import SpectrumSoakView

        c.register_factory(CfgCalibrationViewModel, lambda c: CfgCalibrationViewModel(
            bus=ctx.event_bus,
            dispatcher=c.get(QtDispatcher),
            handle=c.get(PhaseStabilizationHandle),
            config=c.get(StabilizationConfig),
            cfg_range=c.get(CfgRange),
        ))
        c.register_factory(CfgCalibrationView, lambda c: CfgCalibrationView(c.get(CfgCalibrationViewModel), parent=None))

        c.register_factory(XcorrViewModel, lambda c: XcorrViewModel(
            bus=ctx.event_bus,
            dispatcher=c.get(QtDispatcher),
            probe=c.get(Fms300ppHandle),
            delay=c.get(MfaccHandle),
            grating=c.get(Uts150ccHandle),
            scope=c.get(OscilloscopeWorkerHandle),
            spectrometer=c.get(SpectrometerWorkerHandle),
            settings=c.get(XcorrSettings),
        ))
        c.register_factory(XcorrView, lambda c: XcorrView(c.get(XcorrViewModel), parent=None))

        # Singleton settings so the panel keeps its duration/period across open/close,
        # the way XcorrSettings does. The StabilizationConfig is injected READ-ONLY --
        # the soak records what the loop is doing, it never configures it.
        c.register_singleton(SoakSettings, lambda _: SoakSettings())
        c.register_factory(SpectrumSoakViewModel, lambda c: SpectrumSoakViewModel(
            bus=ctx.event_bus,
            dispatcher=c.get(QtDispatcher),
            spectrometer=c.get(SpectrometerWorkerHandle),
            phase=c.get(PhaseStabilizationHandle),
            config=c.get(StabilizationConfig),
            settings=c.get(SoakSettings),
        ))
        c.register_factory(SpectrumSoakView,
                           lambda c: SpectrumSoakView(c.get(SpectrumSoakViewModel), parent=None))

    @staticmethod
    def _register_xcorr(c: Container, ctx: AppContext) -> None:
        """Register the XCORR routine. **No Qt** — this half must stay importable
        and resolvable from the headless harness, which never builds a window.

        The config is a singleton so the (future) UI and the headless runner
        configure the same object. ``XcorrRoutine`` is a *factory*: ``BaseRoutine``
        starts a serial ``TaskRunner`` thread in its constructor, so each ``get()``
        must produce a fresh instance rather than resurrecting a stopped one.

        Note there is no in-repo precedent for a routine in DI at all —
        ``CfgCalibrationRoutine`` is constructed directly by its ViewModel, which is
        exactly the coupling that makes it undriveable headlessly.
        """
        # The UI edits this mutable twin and freezes it into an XcorrConfig on Start
        # (the config is frozen and cannot be bound to the form). Singleton so the
        # panel keeps its values across open/close within a session.
        c.register_singleton(XcorrSettings, lambda _: XcorrSettings())

        c.register_singleton(XcorrConfig, lambda _: XcorrConfig(
            # Placeholders. The headless runner and the eventual UI both override
            # these; a scan is never launched on defaults.
            probe_start_mm=75.0, probe_stop_mm=75.0, probe_step_mm=1.0,
            grating_start_mm=-30.0, grating_stop_mm=-30.0, grating_step_mm=1.0,
            delay_base_start_mm=18.0, delay_base_stop_mm=18.0, delay_base_step_mm=1.0,
            out_dir=Path.cwd() / "xcorr_runs",
        ))

        c.register_factory(XcorrRoutine, lambda c: XcorrRoutine(
            bus=ctx.event_bus,
            config=c.get(XcorrConfig),
            probe=c.get(Fms300ppHandle),
            delay=c.get(MfaccHandle),
            grating=c.get(Uts150ccHandle),
            scope=c.get(OscilloscopeWorkerHandle),
            spectrometer=c.get(SpectrometerWorkerHandle),
        ))
