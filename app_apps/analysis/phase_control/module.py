from __future__ import annotations

from app_apps.analysis.phase_control.envelope_handle import EnvelopeHandle
from app_apps.analysis.phase_control.phase_tracking_handle import PhaseTrackingHandle
from app_apps.analysis.phase_control.service import PhaseControlService
from app_apps.analysis.phase_control.subprocess.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from spm_002.buffer import SpectrumMemorySpec
from app_apps.io.spectrometer.module import SpectrometerModule
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle
from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule


class PhaseControlModule(BaseModule):
    name = "phase_control"
    requires = (SpectrometerModule,)

    def register(self, c: Container, ctx: AppContext) -> None:
        spec = c.get(SpectrumMemorySpec)
        writer = c.get(SpectrometerWorkerHandle)
        config = StabilizationConfig()
        envelope_config = EnvelopeConfig()

        phase_tracking_handle = PhaseTrackingHandle(bus=ctx.event_bus, spectrum_writer=writer, config=config)
        envelope_handle = EnvelopeHandle(bus=ctx.event_bus, spectrum_writer=writer)

        service = PhaseControlService(
            bus=ctx.event_bus,
            spec=spec,
            phase_tracking_handle=phase_tracking_handle,
            envelope_handle=envelope_handle,
            config=config,
        )

        c.register_instance(StabilizationConfig, config)
        c.register_instance(EnvelopeConfig, envelope_config)
        c.register_instance(PhaseControlService, service)
        c.register_instance(PhaseTrackingHandle, phase_tracking_handle)
        c.register_instance(EnvelopeHandle, envelope_handle)

        from app_apps.analysis.phase_control.ui.stabilization_control_vm import StabilizationControlVM
        from app_apps.analysis.phase_control.ui.envelope_control_vm import EnvelopeControlVM
        from app_apps.analysis.phase_control.ui.phase_control_vm import PhaseControlVM
        from app_apps.analysis.phase_control.ui.phase_control_panel import PhaseControlPanel
        from base_qt.app.dispatcher import QtDispatcher

        c.register_factory(StabilizationControlVM, lambda c: StabilizationControlVM(
            ctx.event_bus,
            c.get(QtDispatcher),
            c.get(PhaseTrackingHandle),
            c.get(StabilizationConfig),
        ))
        c.register_factory(EnvelopeControlVM, lambda c: EnvelopeControlVM(
            ctx.event_bus,
            c.get(QtDispatcher),
            c.get(EnvelopeHandle),
        ))
        c.register_factory(PhaseControlVM, lambda c: PhaseControlVM(
            ctx.event_bus,
            c.get(QtDispatcher),
            c.get(PhaseControlService),
            c.get(SpectrometerWorkerHandle),
            c.get(StabilizationControlVM),
            c.get(EnvelopeControlVM),
        ))
        c.register_factory(PhaseControlPanel, lambda c: PhaseControlPanel(c.get(PhaseControlVM)))

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        service = c.get(PhaseControlService)
        service.start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        phase_tracking_handle = c.get(PhaseTrackingHandle)
        envelope_handle = c.get(EnvelopeHandle)
        service = c.get(PhaseControlService)
        phase_tracking_handle.pause()
        envelope_handle.pause()
        service.stop()
