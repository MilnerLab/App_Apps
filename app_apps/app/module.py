# your_app/modules/shell/module.py
from __future__ import annotations

from app_apps.app.ui.main_window_view import MainWindowView
from app_apps.app.ui.main_window_vm import MainWindowVM
from app_apps.app.ui.menu_bar_VM import MenuBarVM
from app_apps.app.ui.menu_bar_view import MenuBarView
from base_core.framework.app import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from base_qt.app.interfaces import IUiDispatcher
from base_qt.views.registry.enums import ViewKind
from base_qt.views.registry.interfaces import IViewRegistry
from base_qt.views.registry.models import ViewSpec
from base_qt.views.registry.view_registry import ViewRegistry


class AppModule(BaseModule):
    """
    Shell module:
    - provides the global ViewRegistry
    - registers the app menu bar (MENUBAR ViewSpec)
    - registers the app main window (factory)
    """

    name = "shell"
    requires = ()

    def register(self, c: Container, ctx: AppContext) -> None:
        # --- Registry (singleton) ------------------------------------------
        c.register_singleton(IViewRegistry, lambda c: ViewRegistry())

        # --- Shell VM + MenuBar -------------------------------------------
        c.register_factory(MainWindowVM, lambda c: MainWindowVM(c.get(IUiDispatcher), ctx.event_bus))
        c.register_factory(MenuBarVM, lambda c: MenuBarVM())
        
        # If you want exactly one menubar instance, you can register it as singleton.
        c.register_singleton(MenuBarView, lambda c: MenuBarView(c.get(MenuBarVM), c.get(IViewRegistry)))

        # Register MENUBAR view spec (MainWindowViewBase will pick this up automatically)
        reg = c.get(IViewRegistry)
        reg.register(
            ViewSpec(
                id=MenuBarView.id(),
                title="MenuBar",
                kind=ViewKind.MENUBAR,
                factory=lambda: c.get(MenuBarView),
                order=0,
            )
        )

        # --- Main window ---------------------------------------------------
        c.register_factory(MainWindowView, lambda c: MainWindowView(
            vm=c.get(MainWindowVM),
            registry=c.get(IViewRegistry),
        ))
