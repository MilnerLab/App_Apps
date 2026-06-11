from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QLabel

from base_core.framework.di import Container
from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.apply import install_ui
from base_qt.ui.lab_main_window import LabMainWindow


class AppShell(LabMainWindow):
    """
    Main application window.

    Hosts the status area (app messages) and the File / Settings menus.
    The central widget is a placeholder until the main-window content
    is implemented.

    Closing this window quits the application; the panel window is not
    independently closeable.
    """

    def __init__(
        self,
        container: Container,
        bus: EventBus,
        dispatcher: QtDispatcher,
    ) -> None:
        super().__init__("Phase Control Lab", bus, dispatcher)
        self.resize(500, 350)
        install_ui(QApplication.instance())

        label = QLabel("Coming soon")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(label)

        from app_apps.app.panel_window import AppPanelWindow
        self._panel_window = AppPanelWindow(container, bus, dispatcher)
        self._panel_window.show()

    def closeEvent(self, event: QCloseEvent) -> None:
        # Bypass PanelWindow.closeEvent (which ignores) and destroy it directly.
        self._panel_window.destroy()
        super().closeEvent(event)
