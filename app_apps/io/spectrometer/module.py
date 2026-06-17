from __future__ import annotations

from app_apps.io.spectrometer.buffer import SpectrumBuffer, SpectrumMemorySpec
from app_apps.io.spectrometer.service import SpectrometerService
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle
from base_core.framework.app.context import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from spm_002.config import PYTHON32_PATH


class SpectrometerModule(BaseModule):
    name = "spectrometer"

    def register(self, c: Container, ctx: AppContext) -> None:
        spec = SpectrumMemorySpec("spectrum_spm002")
        c.register_instance(SpectrumMemorySpec, spec)

        service = SpectrometerService(
            bus=ctx.event_bus,
            python_exe=PYTHON32_PATH,
        )

        handle = SpectrometerWorkerHandle(bus=ctx.event_bus, spec=spec)
        service.add_buffer(SpectrumBuffer, spec)
        service.add_handle(handle)

        c.register_instance(SpectrometerService, service)
        c.register_instance(SpectrometerWorkerHandle, handle)

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        service = c.get(SpectrometerService)
        handle = c.get(SpectrometerWorkerHandle)
        service.start()
        handle.start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        handle = c.get(SpectrometerWorkerHandle)
        service = c.get(SpectrometerService)
        handle.stop()
        service.stop()
