from __future__ import annotations

from PySide6.QtCore import Qt

from base_core.framework.di import Container
from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_window import PanelWindow


class AppPanelWindow(PanelWindow):
    """
    Application panel window.

    Registers all app-specific dockable panels.  Extend _build_panels
    as new panels are added.
    """

    def __init__(
        self,
        container: Container,
        bus: EventBus,
        dispatcher: QtDispatcher,
    ) -> None:
        super().__init__("Panels")
        self.resize(1100, 750)
        self._build_panels(container, bus, dispatcher)

    def _build_panels(
        self,
        container: Container,
        bus: EventBus,
        dispatcher: QtDispatcher,
    ) -> None:
        from app_apps.analysis.phase_control.service import PhaseControlService

        c = container

        def make_phase_control():
            from app_apps.analysis.phase_control.ui.phase_control_panel import PhaseControlPanel
            from app_apps.analysis.phase_control.ui.phase_control_vm import PhaseControlVM
            from app_apps.io.spectrometer.service import SpectrometerService
            from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer
            return PhaseControlPanel(
                PhaseControlVM(
                    bus, dispatcher,
                    c.get(PhaseControlService),
                    c.get(SpectrometerService).output,
                    c.get(SharedSpectrumBuffer),
                )
            )

        self.register_panel(
            "Phase Control",
            make_phase_control,
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
