# modules/menu_module/layout/layout_service.py
from __future__ import annotations
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QMessageBox
from LabviewToPython.core.types.host import HostServices

class LayoutService:
    """Encapsulates save/restore/reset logic (isolated from UI and VM)."""
    def __init__(self, host: HostServices) -> None:
        self._host = host

    def save(self) -> None:
        s = QSettings()
        s.setValue("geometry", self._host.window.saveGeometry())
        s.setValue("windowState", self._host.window.saveState())
        s.sync()

    def restore(self) -> None:
        s = QSettings()
        geom = s.value("geometry")
        state = s.value("windowState")
        if geom is not None:
            self._host.window.restoreGeometry(geom)
        if state is not None and not self._host.window.restoreState(state):
            QMessageBox.warning(self._host.window, "Layout", "Could not restore previous layout.")

    def reset(self) -> None:
        # Show everything and re-dock to a sensible default
        for d in self._host.get_all_docks():
            d.setFloating(False); d.show()
            area = (Qt.DockWidgetArea.LeftDockWidgetArea
                    if ("Scan" in d.windowTitle() or "Measurement" in d.windowTitle())
                    else Qt.DockWidgetArea.RightDockWidgetArea)
            self._host.window.addDockWidget(area, d)
        # simple tabify grouping
        left = [d for d in self._host.get_all_docks() if ("Scan" in d.windowTitle() or "Measurement" in d.windowTitle())]
        right = [d for d in self._host.get_all_docks() if d not in left]
        for stack in (left, right):
            for a, b in zip(stack, stack[1:]):
                self._host.window.tabifyDockWidget(a, b)
            if stack: stack[0].raise_()
        # clear persisted state
        s = QSettings(); s.remove("geometry"); s.remove("windowState")

    def show_all(self) -> None:
        for d in self._host.get_all_docks(): d.show()
