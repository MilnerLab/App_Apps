from __future__ import annotations
from PySide6.QtCore import QObject, Signal, Property, Slot
from LabviewToPython.core.domain.enums.appearance import Appearance
from ..menu_services.theme_service import ThemeService


class AppearanceViewModel(QObject):
    """ViewModel for the Appearance page (Dark/Light)."""

    themeChanged = Signal()

    def __init__(self, theme: ThemeService) -> None:
        super().__init__()
        self._theme = theme

    @property
    def is_dark(self) -> bool:
        return self._theme.appearance == Appearance.DARK

    # ---- Commands for the DualChoiceButton ----
    def setDark(self) -> None:
        self._theme.set(Appearance.DARK)

    def setLight(self) -> None:
        self._theme.set(Appearance.LIGHT)
