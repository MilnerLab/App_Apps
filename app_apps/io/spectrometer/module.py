from __future__ import annotations

from app_apps.io.spectrometer.service import SpectrometerService
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle
from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from spm_002.buffer import SpectrumBuffer, SpectrumMemorySpec
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Time
from spm_002.config import PYTHON32_PATH, SpectrometerConfig


class SpectrometerModule(BaseModule):
    name = "spectrometer"

    def register(self, c: Container, ctx: AppContext) -> None:
        spec = SpectrumMemorySpec("spectrum_spm002")
        c.register_instance(SpectrumMemorySpec, spec)

        # 100 ms is the lab's working exposure, not spm_002's 50 ms default. Set here
        # rather than in the device library: it is a property of this instrument and
        # this experiment, and spm_002 is shared.
        config = SpectrometerConfig(exposure_time=Time(100, Prefix.MILLI))
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

        from app_apps.io.spectrometer.ui.spectrometer_view_model import SpectrometerViewModel
        from app_apps.io.spectrometer.ui.spectrometer_view import SpectrometerView
        from base_qt.app.dispatcher import QtDispatcher
        c.register_factory(SpectrometerViewModel, lambda c: SpectrometerViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(SpectrometerWorkerHandle), c.get(SpectrometerConfig)
        ))
        c.register_factory(SpectrometerView, lambda c: SpectrometerView(c.get(SpectrometerViewModel), parent=None))

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
