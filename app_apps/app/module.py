from __future__ import annotations

from base_core.framework.app import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from base_qt.app.dispatcher import QtDispatcher
from base_qt.app.interfaces import IUiDispatcher


class AppModule(BaseModule):
    """
    Shell module: registers the Qt dispatcher.
    AppShell is constructed directly in app.py after modules bootstrap.
    """

    name = "shell"
    requires = ()

    def register(self, c: Container, ctx: AppContext) -> None:
        c.register_singleton(IUiDispatcher, lambda _: QtDispatcher())
        c.register_singleton(QtDispatcher,  lambda c: c.get(IUiDispatcher))
