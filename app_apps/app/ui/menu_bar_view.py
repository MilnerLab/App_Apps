from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QWidget

from base_qt.views.bases.menu_view_base import MenuViewBase

from .menu_bar_VM import MenuBarVM


class MenuBarView(MenuViewBase[MenuBarVM]):
    def __init__(self, vm: MenuBarVM, parent: Optional[QWidget] = None) -> None:
        super().__init__(vm=vm, parent=parent)