from __future__ import annotations

from app_apps.io.oscilloscope.buffer import ScopeBuffer, ScopeMemorySpec
from app_apps.io.oscilloscope.oscilloscope_worker_handler import OscilloscopeWorkerHandle
from app_apps.io.oscilloscope.service import OscilloscopeService
from base_core.framework.app.context import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule


class OscilloscopeModule(BaseModule):
    name = "oscilloscope"

    def register(self, c: Container, ctx: AppContext) -> None:
        spec = ScopeMemorySpec("scope_tbs2012c")
        c.register_instance(ScopeMemorySpec, spec)

        service = OscilloscopeService(bus=ctx.event_bus)
        handle = OscilloscopeWorkerHandle(bus=ctx.event_bus, spec=spec)
        service.add_buffer(ScopeBuffer, spec)
        service.add_handle(handle)

        c.register_instance(OscilloscopeService, service)
        c.register_instance(OscilloscopeWorkerHandle, handle)

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        service = c.get(OscilloscopeService)
        handle = c.get(OscilloscopeWorkerHandle)
        service.start()
        handle.start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        c.get(OscilloscopeWorkerHandle).stop()
        c.get(OscilloscopeService).stop()
