# LabviewToPython/modules/menu/module.py
from __future__ import annotations
from typing import Optional

from PySide6.QtWidgets import QWidget

from LabviewToPython.core.types.host import AppContext, HostServices, DockSpec
from LabviewToPython.modules.common.bases import MenuModuleBase
from LabviewToPython.core.di.container import Container

from .menu_services.theme_service import ThemeService
from .menu_services.units_service import UnitsService
from .menu_services.layout_service import LayoutService

from .viewmodels.menubar_vm import MenubarViewModel
from .views.menubar_view import MenubarView

from .viewmodels.app_settings_vm import AppSettingsViewModel
from .views.app_settings_view import AppSettingsDialog

from .viewmodels.layout_vm import LayoutViewModel
from .views.layout_view import LayoutDialog


class MenuModule(MenuModuleBase):
    def __init__(self) -> None:
        super().__init__()
        # private Optionals
        self._theme: Optional[ThemeService] = None
        self._units: Optional[UnitsService] = None
        self._layout: Optional[LayoutService] = None

        self._app_vm: Optional[AppSettingsViewModel] = None
        self._layout_vm: Optional[LayoutViewModel] = None

        self._app_dlg: Optional[AppSettingsDialog] = None
        self._layout_dlg: Optional[LayoutDialog] = None

    # ------- Non-optional Properties with guards -------
    @property
    def theme(self) -> ThemeService:
        assert self._theme is not None, "ThemeService not initialized (call start(ctx) first)."
        return self._theme

    @property
    def units(self) -> UnitsService:
        assert self._units is not None, "UnitsService not initialized (call start(ctx) first)."
        return self._units

    @property
    def layout(self) -> LayoutService:
        assert self._layout is not None, "LayoutService not initialized (call install_menus(host) first)."
        return self._layout

    @property
    def app_vm(self) -> AppSettingsViewModel:
        assert self._app_vm is not None, "AppSettings VM not built (install_menus(host))."
        return self._app_vm

    @property
    def layout_vm(self) -> LayoutViewModel:
        assert self._layout_vm is not None, "Layout VM not built (install_menus(host))."
        return self._layout_vm

    # ------- Lifecycle -------
    def start(self, ctx: AppContext) -> None:
        super().start(ctx)
        self._theme = ThemeService()
        self._units = UnitsService()
        # global & typ-sicher registrieren
        Container.register(UnitsService, self._units)

    def install_menus(self) -> None:
        # Services/VMs, die den Host brauchen:
        self._layout = LayoutService(self.host)
        self._app_vm = AppSettingsViewModel()
        self._layout_vm = LayoutViewModel(self.layout)

        # Dialog-Opener nutzen Properties -> non-optional Types
        def open_app() -> None:
            if self._app_dlg is None:
                self._app_dlg = AppSettingsDialog(vm=self.app_vm, theme=self.theme, units=self.units, parent=self.host.window)
                self._app_dlg.resize(900, 560)
            self._app_dlg.show(); self._app_dlg.raise_(); self._app_dlg.activateWindow()

        def open_layout() -> None:
            if self._layout_dlg is None:
                self._layout_dlg = LayoutDialog(vm=self.layout_vm, parent=self.host.window)
                self._layout_dlg.resize(320, 220)
            self._layout_dlg.show(); self._layout_dlg.raise_(); self._layout_dlg.activateWindow()

        MenubarView.build(self.host, MenubarViewModel(host=self.host, open_app_settings=open_app, open_layout=open_layout))
        self.layout.restore()
