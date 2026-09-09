from __future__ import annotations

from app_apps.io.oscilloscope.oscilloscope_worker_handler import OscilloscopeWorkerHandle
from app_apps.io.oscilloscope.service import OscilloscopeService
from base_core.framework.app.context import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from oscilloscope.buffer import ScopeBuffer, ScopeMemorySpec
from oscilloscope.config import ScopeConfig


class OscilloscopeModule(BaseModule):
    """Registers the oscilloscope subprocess service, its shared-memory buffer and handle.

    ``on_startup`` spawns the subprocess but does **not** start the worker, so opening the
    VISA device never happens as a silent side effect of app launch -- a wedged USBTMC link
    hangs VISA enumeration itself, and that belongs to whoever asked for the scope, not to
    every launch. The worker is started from the Devices > Oscilloscope panel, or by the
    XCORR routine when a scan begins.
    """

    name = "oscilloscope"

    def register(self, c: Container, ctx: AppContext) -> None:
        # One shared config instance: the panel form edits the same object the handle sends.
        config = ScopeConfig()
        c.register_instance(ScopeConfig, config)

        spec = ScopeMemorySpec("scope_tbs2012c")
        c.register_instance(ScopeMemorySpec, spec)

        service = OscilloscopeService(bus=ctx.event_bus)
        handle = OscilloscopeWorkerHandle(bus=ctx.event_bus, spec=spec, config=config)
        # add_buffer before add_handle: WriterWorkerHandle creates the segment on bind and
        # the subprocess is told to attach it in the order the service registered them.
        service.add_buffer(ScopeBuffer, spec)
        service.add_handle(handle)

        c.register_instance(OscilloscopeService, service)
        c.register_instance(OscilloscopeWorkerHandle, handle)

        from app_apps.io.oscilloscope.ui.oscilloscope_view import OscilloscopeView
        from app_apps.io.oscilloscope.ui.oscilloscope_view_model import OscilloscopeViewModel
        from base_qt.app.dispatcher import QtDispatcher
        c.register_factory(OscilloscopeViewModel, lambda c: OscilloscopeViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(OscilloscopeWorkerHandle), c.get(ScopeConfig)
        ))
        c.register_factory(OscilloscopeView, lambda c: OscilloscopeView(
            c.get(OscilloscopeViewModel), parent=None
        ))

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        # Spawn the subprocess so the connector/handle are bound and the buffer exists; the
        # worker is started by whoever needs it (the panel, or the XCORR routine), not here.
        c.get(OscilloscopeService).start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        c.get(OscilloscopeWorkerHandle).pause()
        c.get(OscilloscopeService).stop()
