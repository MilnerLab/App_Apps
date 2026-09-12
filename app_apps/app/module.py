from __future__ import annotations

from base_core.framework.app import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from base_qt.app.dispatcher import QtDispatcher
from base_qt.app.interfaces import IUiDispatcher


class AppModule(BaseModule):
    """
    Shell module: registers the Qt dispatcher and the cross-cutting Devices page.
    AppShell is constructed directly in app.py after modules bootstrap.
    """

    name = "shell"
    requires = ()

    def register(self, c: Container, ctx: AppContext) -> None:
        c.register_singleton(IUiDispatcher, lambda _: QtDispatcher())
        c.register_singleton(QtDispatcher,  lambda c: c.get(IUiDispatcher))

        # Registered here rather than in a device module: the page spans every device in
        # io/ -- spectrometer and oscilloscope as well as control-readout -- so no single
        # device module owns it. The factory is lazy, so the view models it pulls need
        # only be registered by the time the dock first shows it, not by the time this runs.
        from app_apps.io.ui.devices_view import DevicesView
        c.register_factory(DevicesView, lambda c: DevicesView(c))
