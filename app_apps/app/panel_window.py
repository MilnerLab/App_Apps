from __future__ import annotations

from PySide6.QtCore import Qt

from base_core.framework.di import Container
from base_qt.ui.panel_window import PanelWindow


class AppPanelWindow(PanelWindow):
    """
    Application panel window.

    Registers all app-specific dockable panels.  Extend _build_panels
    as new panels are added.
    """

    def __init__(self, container: Container) -> None:
        super().__init__("Panels")
        self.resize(1100, 750)
        self._build_panels(container)

    def _build_panels(self, container: Container) -> None:
        from app_apps.analysis.phase_control.ui.phase_control_panel import PhaseControlPanel

        c = container
        self.register_panel(
            "Phase Control",
            lambda: c.get(PhaseControlPanel),
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
