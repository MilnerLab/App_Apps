from __future__ import annotations

from app_apps.io.oscilloscope.oscilloscope_worker_handler import OscilloscopeWorkerHandle
from app_apps.io.oscilloscope.service import OscilloscopeService
from base_core.framework.app.context import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule


class OscilloscopeModule(BaseModule):
    """Registers the oscilloscope subprocess service and its request/reply handle.

    No shared-memory buffer (B14). ``on_startup`` spawns the subprocess but does **not**
    start the worker — like the stages (A11/G12), the XCORR routine starts the scope
    worker itself and waits for RUNNING, so opening the VISA device never happens as a
    silent side effect of app launch.
    """

    name = "oscilloscope"

    def register(self, c: Container, ctx: AppContext) -> None:
        service = OscilloscopeService(bus=ctx.event_bus)
        handle = OscilloscopeWorkerHandle(bus=ctx.event_bus)
        service.add_handle(handle)

        c.register_instance(OscilloscopeService, service)
        c.register_instance(OscilloscopeWorkerHandle, handle)

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        # Spawn the subprocess so the connector/handle are bound; the worker is started
        # by whoever needs it (the XCORR routine), not here.
        c.get(OscilloscopeService).start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        c.get(OscilloscopeWorkerHandle).pause()
        c.get(OscilloscopeService).stop()
