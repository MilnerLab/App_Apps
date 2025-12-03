# modules/menu/viewmodels/app_settings_vm.py
from PySide6.QtCore import QObject, Signal, Property

class AppSettingsViewModel(QObject):
    sectionsChanged = Signal()
    selectedIndexChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._sections: list[str] = ["Appearance", "Units"]
        self._selected = 0

    @Property(list, notify=sectionsChanged)            # <-- statt "QStringList"
    def sections(self) -> list[str]:
        return self._sections

    @Property(int, notify=selectedIndexChanged)
    def selectedIndex(self) -> int:
        return self._selected

    def setSelectedIndex(self, idx: int) -> None:
        if idx != self._selected:
            self._selected = idx
            self.selectedIndexChanged.emit()
