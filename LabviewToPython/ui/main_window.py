# labapp/views/main_window.py
from __future__ import annotations
from typing import Dict, Iterable, List
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QWidget, QDockWidget
from LabviewToPython.core.runtime.module_manager import ModuleManager
from LabviewToPython.core.events.eventbus import IEventBus
from LabviewToPython.core.types.host import AppContext, DockSpec, HostServices

class MainWindow(QMainWindow):
    def __init__(self, modules: ModuleManager, bus: IEventBus) -> None:
        super().__init__()
        self.setWindowTitle("LabApp")
        self.resize(1280, 800)
        self.setCentralWidget(QWidget(self))
        self._docks: Dict[str, QDockWidget] = {}

        host = HostServices(
            window=self,
            menubar=self.menuBar(),
            add_dock=self._add_dock,
            get_all_docks=lambda: self._docks.values(),
        )
        ctx = AppContext(bus=bus, host=host)

        # 1) Services/Threads der Module starten
        modules.start_all(ctx)
        # 2) Docks erstellen
        modules.create_all_docks(host)
        # 3) Menüs installieren
        modules.install_all_menus()

        self._auto_tabify()

    def _add_dock(self, spec: DockSpec) -> QDockWidget:
        dock = QDockWidget(spec.title, self)
        dock.setObjectName(spec.object_name)
        dock.setWidget(spec.factory(dock))
        features = (QDockWidget.DockWidgetFeature.DockWidgetMovable
                    | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                    | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        dock.setFeatures(features)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.addDockWidget(spec.area, dock)
        self._docks[spec.object_name] = dock
        return dock

    def _auto_tabify(self) -> None:
        left, right = [], []
        for d in self._docks.values():
            (left if ("Scan" in d.windowTitle() or "Measurement" in d.windowTitle()) else right).append(d)
        for stack in (left, right):
            for a, b in zip(stack, stack[1:]): self.tabifyDockWidget(a, b)
            if stack: stack[0].raise_()

    def closeEvent(self, e) -> None:
        super().closeEvent(e)
