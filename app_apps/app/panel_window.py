from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabWidget

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
        from app_apps.analysis.phase_control.ui.phase_control_view import PhaseControlView
        from app_apps.analysis.xcorr.ui.xcorr_display_view import XcorrDisplayView

        c = container
        # Both panels go into the *same* dock area and are then tabbed on top of each
        # other (like stacked windows) rather than shown side by side — each gets the
        # full width when active, which is what these plot-heavy panels want. The user
        # can still drag a tab out to place them side by side if they ever need to.
        phase = self.register_panel(
            "Phase Control",
            lambda: c.get(PhaseControlView),
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        xcorr = self.register_panel(
            "XCORR Display",
            lambda: c.get(XcorrDisplayView),
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.tabifyDockWidget(phase, xcorr)
        # Tabs on top so it is obvious there is more than one panel stacked here (the
        # default bottom tab bar is easy to miss).
        self.setTabPosition(Qt.DockWidgetArea.LeftDockWidgetArea, QTabWidget.TabPosition.North)
        phase.raise_()  # show Phase Control's tab first
