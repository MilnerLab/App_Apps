from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from base_core.framework.di import Container
from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.apply import install_ui
from base_qt.ui.lab_main_window import LabMainWindow


class AppShell(LabMainWindow):
    """
    Main application window.

    Hosts the status area (app messages) and the File / Settings menus.
    The central widget is the VMI ion view when the camera module is loaded, and a
    placeholder otherwise.

    Closing this window quits the application; the panel window is not
    independently closeable.
    """

    def __init__(
        self,
        container: Container,
        bus: EventBus,
        dispatcher: QtDispatcher,
    ) -> None:
        super().__init__("App Apps", bus, dispatcher)
        self.resize(900, 700)
        install_ui(QApplication.instance())

        self.setCentralWidget(self._build_central(container))

        self._build_devices_menu(container)
        self._build_routines_menu(container)

        from app_apps.app.panel_window import AppPanelWindow
        self._panel_window = AppPanelWindow(container)
        self._panel_window.show()

    def _build_central(self, container: Container) -> QWidget:
        from app_apps.io.camera_vmi.ui.ion_view import IonView

        # Pinned here rather than docked: the ion image is what the main window is for. It
        # carries its own Config button for the ion detection settings.
        if container.is_registered(IonView):
            return container.get(IonView)
        label = QLabel("VMI camera module not loaded")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _build_devices_menu(self, container: Container) -> None:
        from base_qt.ui.view_host import ViewHost
        from app_apps.io.spectrometer.ui.spectrometer_view import SpectrometerView
        from app_apps.io.oscilloscope.ui.oscilloscope_view import OscilloscopeView
        from app_apps.io.control_readout.ell14.ui.view import ELL14RotatorView
        from app_apps.io.control_readout.fms300pp.ui.view import Fms300ppView
        from app_apps.io.control_readout.mfa_cc.ui.view import MfaccView
        from app_apps.io.control_readout.uts150cc.ui.view import Uts150ccView
        from app_apps.io.control_readout.rgv.ui.view import RgvView
        from app_apps.io.control_readout.picomotor.ui.view import PicomotorView
        from app_apps.io.camera_vmi.ui.config_view import CameraVmiDeviceView

        menu = self.menuBar().addMenu("Devices")
        self._spectrometer_host = ViewHost(container, SpectrometerView, parent=self)
        self._oscilloscope_host = ViewHost(container, OscilloscopeView, parent=self)
        self._ell14_host = ViewHost(container, ELL14RotatorView, parent=self)
        self._fms300pp_host = ViewHost(container, Fms300ppView, parent=self)
        self._mfa_cc_host = ViewHost(container, MfaccView, parent=self)
        self._uts150cc_host = ViewHost(container, Uts150ccView, parent=self)
        self._rgv_host = ViewHost(container, RgvView, parent=self)
        self._picomotor_host = ViewHost(container, PicomotorView, parent=self)

        # Only when the camera module is loaded: resolving the view would fail otherwise.
        if container.is_registered(CameraVmiDeviceView):
            self._camera_vmi_host = ViewHost(container, CameraVmiDeviceView, parent=self)
            menu.addAction("VMI Camera", self._camera_vmi_host.open)
        menu.addAction("Spectrometer", self._spectrometer_host.open)
        menu.addAction("Oscilloscope", self._oscilloscope_host.open)
        menu.addAction("ELL14 Rotator", self._ell14_host.open)
        menu.addAction("FMS300PP Stage", self._fms300pp_host.open)
        menu.addAction("MFA-CC Stage", self._mfa_cc_host.open)
        menu.addAction("UTS150CC Stage", self._uts150cc_host.open)
        menu.addAction("RGV100BL HWP", self._rgv_host.open)
        menu.addAction("Mirror Picomotors", self._picomotor_host.open)

    def _build_routines_menu(self, container: Container) -> None:
        from base_qt.ui.view_host import ViewHost
        from app_apps.routines.cfg_calibration.ui.view import CfgCalibrationView
        from app_apps.routines.xcorr.ui.view import XcorrView

        menu = self.menuBar().addMenu("Routines")
        self._cfg_calibration_host = ViewHost(container, CfgCalibrationView, parent=self)
        self._xcorr_host = ViewHost(container, XcorrView, parent=self)

        menu.addAction("CFG Calibration", self._cfg_calibration_host.open)
        menu.addAction("XCORR Scan", self._xcorr_host.open)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Bypass PanelWindow.closeEvent (which ignores) and destroy it directly.
        self._panel_window.destroy()
        super().closeEvent(event)
