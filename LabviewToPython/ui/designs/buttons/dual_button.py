from __future__ import annotations
from PySide6.QtWidgets import (QHBoxLayout, QPushButton,
    QButtonGroup, QFrame
)
from PySide6.QtCore import Qt



class DualChoiceButton(QFrame):
    """
    Looks like one button split into left/right choices.
    """
    def __init__(self, left: str, right: str, on_left, on_right, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("DualChoice")
        lay = QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)

        self._left = QPushButton(left);  self._right = QPushButton(right)
        for b in (self._left, self._right):
            b.setCheckable(True); b.setFlat(True); b.setCursor(Qt.CursorShape.PointingHandCursor)

        # group for exclusivity
        grp = QButtonGroup(self); grp.setExclusive(True)
        grp.addButton(self._left); grp.addButton(self._right)

        self._left.clicked.connect(on_left)
        self._right.clicked.connect(on_right)

        lay.addWidget(self._left); lay.addWidget(self._right)

        # styling to look like a single segmented control
        self.setStyleSheet("""
        QFrame#DualChoice { border: 1px solid palette(mid); border-radius: 6px; }
        QFrame#DualChoice QPushButton { padding: 6px 14px; border: none; }
        QFrame#DualChoice QPushButton:checked { background: palette(highlight); color: palette(highlighted-text); }
        QFrame#DualChoice QPushButton:first-child { border-right: 1px solid palette(mid); border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
        QFrame#DualChoice QPushButton:last-child  { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
        """)

    def setChecked(self, left_checked: bool) -> None:
        self._left.setChecked(left_checked)
        self._right.setChecked(not left_checked)