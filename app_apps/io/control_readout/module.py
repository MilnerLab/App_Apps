from __future__ import annotations

from app_apps.io.control_readout.ell14.handler import ELL14RotatorHandle
from app_apps.io.control_readout.service import ControlReadoutService
from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule


class ControlReadoutModule(BaseModule):
    name = "control_readout"

    def register(self, c: Container, ctx: AppContext) -> None:
        service = ControlReadoutService(bus=ctx.event_bus)
        handle = ELL14RotatorHandle(bus=ctx.event_bus)
        service.add_handle(handle)

        c.register_instance(ControlReadoutService, service)
        c.register_instance(ELL14RotatorHandle, handle)

        from app_apps.io.control_readout.ell14.ui.vm import ELL14RotatorVM
        from base_qt.app.dispatcher import QtDispatcher
        c.register_factory(ELL14RotatorVM, lambda c: ELL14RotatorVM(
            ctx.event_bus, c.get(QtDispatcher), c.get(ELL14RotatorHandle)
        ))

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        c.get(ControlReadoutService).start()
        if ctx.status == AppStatus.CONNECTED:
            c.get(ELL14RotatorHandle).start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        c.get(ELL14RotatorHandle).pause()
        c.get(ControlReadoutService).stop()
