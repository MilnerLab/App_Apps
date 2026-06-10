from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from base_core.framework.di import Container
from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.apply import install_ui
from base_qt.ui.lab_main_window import LabMainWindow


class AppShell(LabMainWindow):
    """
    Main application shell for the phase-control lab app.

    Menus (in addition to the base File | Panels | Settings):
      Devices → Spectrometer → Settings…
              → Rotator      → Settings…

    Panels are registered here so the full GUI layout is visible in one place.
    Device-specific panels and dialogs are imported locally to keep the module
    from loading Qt widget code until the shell is actually constructed.
    """

    def __init__(
        self,
        container: Container,
        bus: EventBus,
        dispatcher: QtDispatcher,
    ) -> None:
        super().__init__("Phase Control Lab", bus, dispatcher)
        self.resize(1400, 900)
        install_ui(QApplication.instance())

        self._container  = container
        self._bus        = bus
        self._dispatcher = dispatcher

        self._build_panels()
        self._build_device_menus()

    # ------------------------------------------------------------------
    # Panels
    # ------------------------------------------------------------------

    def _build_panels(self) -> None:
        # Imported here so Qt widget classes aren't loaded at module import time.
        from app_apps.io.spectrometer.ui.spectrum_panel import SpectrumPanel
        from app_apps.io.spectrometer.ui.spectrum_vm import SpectrumVM
        from app_apps.io.control_readout.ui.rotator_panel import RotatorPanel
        from app_apps.io.control_readout.ui.rotator_vm import RotatorVM
        from app_apps.analysis.phase_control.ui.phase_status_panel import PhaseStatusPanel
        from app_apps.analysis.phase_control.ui.phase_status_vm import PhaseStatusVM

        from app_apps.io.spectrometer.service import SpectrometerService
        from app_apps.io.control_readout.service import ControlReadoutService
        from app_apps.analysis.phase_control.service import PhaseControlService
        from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer

        c, bus, d = self._container, self._bus, self._dispatcher

        spectrum_panel = SpectrumPanel(
            SpectrumVM(bus, d, c.get(SpectrometerService), c.get(SharedSpectrumBuffer))
        )
        phase_panel = PhaseStatusPanel(
            PhaseStatusVM(bus, d, c.get(PhaseControlService))
        )
        rotator_panel = RotatorPanel(
            RotatorVM(bus, d, c.get(ControlReadoutService))
        )

        self.register_panel("Spectrum",      spectrum_panel, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.register_panel("Phase Control", phase_panel,    Qt.DockWidgetArea.RightDockWidgetArea, floating=True)
        self.register_panel("Rotator",       rotator_panel,  Qt.DockWidgetArea.RightDockWidgetArea, floating=True)

    # ------------------------------------------------------------------
    # Device menus
    # ------------------------------------------------------------------

    def _build_device_menus(self) -> None:
        devices = self.menuBar().addMenu("Devices")

        m_spec = devices.addMenu("Spectrometer")
        m_spec.addAction("Settings…", self._open_spectrometer_settings)

        m_rot = devices.addMenu("Rotator")
        m_rot.addAction("Settings…", self._open_rotator_settings)

    def _open_spectrometer_settings(self) -> None:
        pass  # TODO: open SpectrometerSettingsDialog

    def _open_rotator_settings(self) -> None:
        pass  # TODO: open RotatorSettingsDialog
