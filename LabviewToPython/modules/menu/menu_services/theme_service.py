from __future__ import annotations
from PySide6.QtCore import QObject
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication

from LabviewToPython.core.domain.enums.appearance import Appearance

class ThemeService(QObject):
    def __init__(self) -> None:
        self._mode = Appearance.DARK
        QApplication.setStyle("Fusion")
        self.apply()

    # -------- public API -------------------------------------------------
    @property
    def appearance(self) -> Appearance:
        return self._mode

    @appearance.setter
    def appearance(self, mode: Appearance) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self.apply()

    def apply(self) -> None:
        """Apply the palette to the current QApplication."""
        if self.appearance == Appearance.DARK:
            QApplication.setPalette(_build_dark_palette())
        else:
            QApplication.setPalette(_build_light_palette())


# -------- palette builders -----------------------------------------------

def _build_light_palette() -> QPalette:
    # Start from Fusion default (already light); tweak minimally
    pal = QPalette()
    # You can tune here if you want slightly different light colors
    return pal


def _build_dark_palette() -> QPalette:
    pal = QPalette()

    # Base greys
    bg = QColor(53, 53, 53)
    alt = QColor(66, 66, 66)
    text = QColor(220, 220, 220)
    disabled_text = QColor(127, 127, 127)

    pal.setColor(QPalette.ColorRole.Window, bg)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
    pal.setColor(QPalette.ColorRole.AlternateBase, alt)
    pal.setColor(QPalette.ColorRole.ToolTipBase, text)
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, bg)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))

    # Accent (you can change this hue globally)
    highlight = QColor(76, 110, 219)
    pal.setColor(QPalette.ColorRole.Highlight, highlight)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))

    # Disabled
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled_text)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled_text)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled_text)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor(80, 80, 80))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, QColor(180, 180, 180))

    return pal
