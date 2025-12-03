# modules/menu_module/layout/layout_vm.py
from __future__ import annotations
from PySide6.QtCore import QObject, Slot
from LabviewToPython.modules.menu.menu_services.layout_service import LayoutService

class LayoutViewModel(QObject):
    def __init__(self, layout: LayoutService) -> None:
        super().__init__()
        self._layout = layout

    @Slot()  # ready for signal/slot connections if you later use QML
    def save(self): self._layout.save()

    @Slot()
    def reset(self): self._layout.reset()

    @Slot()
    def show_all(self): self._layout.show_all()
