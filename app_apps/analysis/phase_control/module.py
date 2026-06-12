from __future__ import annotations

from app_apps.analysis.phase_control.envelope_handle import EnvelopeHandle
from app_apps.analysis.phase_control.phase_tracking_handle import PhaseTrackingHandle
from app_apps.analysis.phase_control.service import PhaseControlService
from app_apps.analysis.phase_control.subprocess.messages import CorrectionAvailable, SpectrumProcessed
from app_apps.io.control_readout.events import RequestRotate
from app_apps.io.spectrometer.buffer import SpectrumMemorySpec
from app_apps.io.spectrometer.events import SpectrumAck
from app_apps.io.spectrometer.module import SpectrometerModule
from app_apps.io.spectrometer.service import SpectrometerService
from base_core.framework.app.context import AppContext
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule


class PhaseControlModule(BaseModule):
    name = "phase_control"
    requires = (SpectrometerModule,)

    def register(self, c: Container, ctx: AppContext) -> None:
        spec = c.get(SpectrumMemorySpec)

        service = PhaseControlService(
            bus=ctx.event_bus,
            io=c.get(TaskRunner),
            spec=spec,
            writer_service=c.get(SpectrometerService),
        )

        service.add_translation(
            CorrectionAvailable,
            lambda msg: RequestRotate(angle=msg.angle, sign=msg.sign),
        )
        service.add_translation(
            SpectrumProcessed,
            lambda msg: SpectrumAck(slot=msg.slot, item_id=msg.item_id, consumer_id=msg.consumer_id),
        )

        phase_tracking_handle = PhaseTrackingHandle(service=service, bus=ctx.event_bus)
        envelope_handle = EnvelopeHandle(service=service, bus=ctx.event_bus)
        service.add_handle(phase_tracking_handle)
        service.add_handle(envelope_handle)

        c.register_instance(PhaseControlService, service)

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        service = c.get(PhaseControlService)
        phase_tracking_handle = c.get(PhaseTrackingHandle)
        envelope_handle = c.get(EnvelopeHandle)
        service.start()
        phase_tracking_handle.start()
        envelope_handle.start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        phase_tracking_handle = c.get(PhaseTrackingHandle)
        envelope_handle = c.get(EnvelopeHandle)
        service = c.get(PhaseControlService)
        phase_tracking_handle.stop()
        envelope_handle.stop()
        service.stop()
