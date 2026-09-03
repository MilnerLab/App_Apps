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
        from app_apps.io.control_readout.ui.devices_view import DevicesView
        from app_apps.routines.spectrum_soak.ui.view import SpectrumSoakView

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
        # Devices joins the same stack rather than sitting beside the plots: it is a page the
        # operator switches TO during alignment and away from while running, not something
        # watched alongside a spectrum.
        devices = self.register_panel(
            "Devices",
            lambda: c.get(DevicesView),
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        # Spectrum Soak joins the same stack: it is watched instead of Phase Control, not
        # beside it -- both want the full width, and the soak's waterfall is the slow
        # answer to the question Phase Control asks frame by frame.
        soak = self.register_panel(
            "Spectrum Soak",
            lambda: c.get(SpectrumSoakView),
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.tabifyDockWidget(phase, xcorr)
        self.tabifyDockWidget(xcorr, devices)
        self.tabifyDockWidget(devices, soak)
        # Tabs on top so it is obvious there is more than one panel stacked here (the
        # default bottom tab bar is easy to miss).
        self.setTabPosition(Qt.DockWidgetArea.LeftDockWidgetArea, QTabWidget.TabPosition.North)
        phase.raise_()  # show Phase Control's tab first
