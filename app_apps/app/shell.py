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

        self._build_devices_menu(container)

        from app_apps.app.panel_window import AppPanelWindow
        self._panel_window = AppPanelWindow(container)
        self._panel_window.show()

    def _build_devices_menu(self, container: Container) -> None:
        from app_apps.io.spectrometer.ui.spectrometer_popout import SpectrometerPopout
        from app_apps.io.spectrometer.ui.spectrometer_vm import SpectrometerVM
        from app_apps.io.control_readout.ell14.ui.popout import ELL14RotatorPopout
        from app_apps.io.control_readout.ell14.ui.vm import ELL14RotatorVM

        menu = self.menuBar().addMenu("Devices")
        self._spectrometer_popout: SpectrometerPopout | None = None
        self._ell14_popout: ELL14RotatorPopout | None = None

        def _open_spectrometer() -> None:
            if self._spectrometer_popout is None:
                self._spectrometer_popout = SpectrometerPopout(
                    container.get(SpectrometerVM), parent=self
                )
            self._spectrometer_popout.open()

        def _open_ell14() -> None:
            if self._ell14_popout is None:
                self._ell14_popout = ELL14RotatorPopout(
                    container.get(ELL14RotatorVM), parent=self
                )
            self._ell14_popout.open()

        menu.addAction("Spectrometer", _open_spectrometer)
        menu.addAction("ELL14 Rotator", _open_ell14)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Bypass PanelWindow.closeEvent (which ignores) and destroy it directly.
        self._panel_window.destroy()
        super().closeEvent(event)
