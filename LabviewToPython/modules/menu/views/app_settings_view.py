# modules/menu/views/app_settings_view.py
from __future__ import annotations
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QSplitter, QListWidget, QListWidgetItem, QStackedWidget
)
from PySide6.QtCore import Qt

from LabviewToPython.modules.menu.viewmodels.app_settings_vm import AppSettingsViewModel
from LabviewToPython.modules.menu.viewmodels.appearance_vm import AppearanceViewModel
from LabviewToPython.modules.menu.menu_services.theme_service import ThemeService
from LabviewToPython.modules.menu.views.appearance_view import AppearanceView

from LabviewToPython.modules.menu.viewmodels.units_vm import UnitsViewModel
from LabviewToPython.modules.menu.menu_services.units_service import UnitsService
from LabviewToPython.modules.menu.views.units_view import UnitsView

class AppSettingsDialog(QDialog):
    def __init__(self, vm: AppSettingsViewModel, theme: ThemeService, units: UnitsService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("App Settings"); self.setModal(False)
        self._vm = vm; self._theme = theme; self._units = units

        root = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal, self)
        root.addWidget(split)

        # left: sections
        self._list = QListWidget()
        for name in self._vm.sections:
            QListWidgetItem(name, self._list)
        split.addWidget(self._list)

        # right: pages
        self._stack = QStackedWidget()
        split.addWidget(self._stack)

        # page 0: Appearance
        appearance_vm = AppearanceViewModel(self._theme)
        self._stack.addWidget(AppearanceView(appearance_vm, parent=self))

        # page 1: Units
        units_vm = UnitsViewModel(self._units)
        self._stack.addWidget(UnitsView(units_vm, parent=self))

        split.setStretchFactor(0, 1); split.setStretchFactor(1, 2)
        split.setSizes([300, 600])

        self._list.currentRowChanged.connect(self._on_section_changed)
        self._list.setCurrentRow(self._vm.selectedIndex)

    def _on_section_changed(self, row: int) -> None:
        self._vm.setSelectedIndex(row)
        self._stack.setCurrentIndex(row)
