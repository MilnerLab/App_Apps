# modules/menu_module/menubar/menubar_view.py
from __future__ import annotations
from PySide6.QtWidgets import QMenu

from LabviewToPython.modules.menu.viewmodels.menubar_vm import MenubarViewModel

class MenubarView:
    @staticmethod
    def build(host, vm: MenubarViewModel) -> None:
        """Create top-level menus and populate with actions from the VM."""
        view_menu: QMenu = host.menubar.addMenu("View")
        settings_menu: QMenu = host.menubar.addMenu("Settings")

        view_actions = vm.build_view_menu_actions()
        if view_actions:
            view_menu.addAction(view_actions[0])  # Show all docks
            view_menu.addSeparator()
            for a in view_actions[1:]:
                view_menu.addAction(a)

        for a in vm.build_settings_menu_actions():
            settings_menu.addAction(a)
