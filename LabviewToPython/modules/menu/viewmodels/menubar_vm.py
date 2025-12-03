# modules/menu_module/menubar/menubar_vm.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Iterable
from PySide6.QtGui import QAction

from LabviewToPython.core.types.host import HostServices


@dataclass
class MenubarViewModel:
    host: HostServices
    open_app_settings: Callable[[], None]
    open_layout: Callable[[], None]

    def build_view_menu_actions(self) -> list[QAction]:
        """Return toggle actions for all docks + 'Show all docks' at top."""
        actions: list[QAction] = []

        act_show_all = QAction("Show all docks", self.host.window)
        act_show_all.triggered.connect(lambda: [d.show() for d in self.host.get_all_docks()])
        actions.append(act_show_all)

        # Separator is handled by the view; return toggles next
        for dock in self.host.get_all_docks():
            t = dock.toggleViewAction()
            t.setText(dock.windowTitle())
            actions.append(t)

        return actions

    def build_settings_menu_actions(self) -> list[QAction]:
        act_app = QAction("App Settings…", self.host.window)
        act_app.setShortcut("Ctrl+,")
        act_app.triggered.connect(self.open_app_settings)

        act_layout = QAction("Layout…", self.host.window)
        act_layout.triggered.connect(self.open_layout)

        return [act_app, act_layout]
