from __future__ import annotations

from app_apps.io.spectrometer.service import SpectrometerService
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle
from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from spm_002.buffer import SpectrumBuffer, SpectrumMemorySpec
from spm_002.config import PYTHON32_PATH, SpectrometerConfig


class SpectrometerModule(BaseModule):
    name = "spectrometer"

    def register(self, c: Container, ctx: AppContext) -> None:
        spec = SpectrumMemorySpec("spectrum_spm002")
        c.register_instance(SpectrumMemorySpec, spec)

        config = SpectrometerConfig()
        c.register_instance(SpectrometerConfig, config)

        service = SpectrometerService(
            bus=ctx.event_bus,
            python_exe=PYTHON32_PATH,
        )

        handle = SpectrometerWorkerHandle(bus=ctx.event_bus, spec=spec, config=config)
        service.add_buffer(SpectrumBuffer, spec)
        service.add_handle(handle)

        c.register_instance(SpectrometerService, service)
        c.register_instance(SpectrometerWorkerHandle, handle)

        from app_apps.io.spectrometer.ui.spectrometer_vm import SpectrometerVM
        from base_qt.app.dispatcher import QtDispatcher
        c.register_factory(SpectrometerVM, lambda c: SpectrometerVM(
            ctx.event_bus, c.get(QtDispatcher), c.get(SpectrometerWorkerHandle), c.get(SpectrometerConfig)
        ))

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        service = c.get(SpectrometerService)
        handle = c.get(SpectrometerWorkerHandle)
        service.start()
        if ctx.status == AppStatus.CONNECTED:
            handle.start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        handle = c.get(SpectrometerWorkerHandle)
        service = c.get(SpectrometerService)
        handle.pause()
        service.stop()
