from __future__ import annotations

from app_apps.io.control_readout.ell14.handler import ELL14RotatorHandle
from app_apps.io.control_readout.fms300pp.handler import Fms300ppHandle
from app_apps.io.control_readout.mfa_cc.handler import MfaccHandle
from app_apps.io.control_readout.rgv.handler import RgvHandle
from app_apps.io.control_readout.service import ControlReadoutService
from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle
from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule

_HANDLE_TYPES = (
    ELL14RotatorHandle,
    Fms300ppHandle,
    MfaccHandle,
    Uts150ccHandle,
    RgvHandle,
)


class ControlReadoutModule(BaseModule):
    name = "control_readout"

    def register(self, c: Container, ctx: AppContext) -> None:
        service = ControlReadoutService(bus=ctx.event_bus)

        for handle_type in _HANDLE_TYPES:
            handle = handle_type(bus=ctx.event_bus)
            service.add_handle(handle)
            c.register_instance(handle_type, handle)

        c.register_instance(ControlReadoutService, service)

        from app_apps.io.control_readout.ell14.ui.view_model import ELL14RotatorViewModel
        from app_apps.io.control_readout.ell14.ui.view import ELL14RotatorView
        from app_apps.io.control_readout.fms300pp.ui.view_model import Fms300ppViewModel
        from app_apps.io.control_readout.fms300pp.ui.view import Fms300ppView
        from app_apps.io.control_readout.mfa_cc.ui.view_model import MfaccViewModel
        from app_apps.io.control_readout.mfa_cc.ui.view import MfaccView
        from app_apps.io.control_readout.rgv.ui.view_model import RgvViewModel
        from app_apps.io.control_readout.rgv.ui.view import RgvView
        from app_apps.io.control_readout.uts150cc.ui.view_model import Uts150ccViewModel
        from app_apps.io.control_readout.uts150cc.ui.view import Uts150ccView
        from base_qt.app.dispatcher import QtDispatcher

        c.register_factory(ELL14RotatorViewModel, lambda c: ELL14RotatorViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(ELL14RotatorHandle)
        ))
        c.register_factory(Fms300ppViewModel, lambda c: Fms300ppViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(Fms300ppHandle)
        ))
        c.register_factory(MfaccViewModel, lambda c: MfaccViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(MfaccHandle)
        ))
        c.register_factory(Uts150ccViewModel, lambda c: Uts150ccViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(Uts150ccHandle)
        ))
        c.register_factory(RgvViewModel, lambda c: RgvViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(RgvHandle)
        ))

        c.register_factory(ELL14RotatorView, lambda c: ELL14RotatorView(c.get(ELL14RotatorViewModel), parent=None))
        c.register_factory(Fms300ppView, lambda c: Fms300ppView(c.get(Fms300ppViewModel), parent=None))
        c.register_factory(MfaccView, lambda c: MfaccView(c.get(MfaccViewModel), parent=None))
        c.register_factory(Uts150ccView, lambda c: Uts150ccView(c.get(Uts150ccViewModel), parent=None))
        c.register_factory(RgvView, lambda c: RgvView(c.get(RgvViewModel), parent=None))

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        if ctx.status == AppStatus.CONNECTED:
            c.get(ControlReadoutService).start()
            
            for handle_type in _HANDLE_TYPES:
                c.get(handle_type).start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        for handle_type in _HANDLE_TYPES:
            c.get(handle_type).pause()
        c.get(ControlReadoutService).stop()
