from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

from LabviewToPython.designs.buttons.dual_button import DualChoiceButton

from ..viewmodels.appearance_vm import AppearanceViewModel

# ⇩ benutze den Pfad, der zu deinem Ordner passt:
# from LabviewToPython.designs.buttons.dual_button import DualChoiceButton

class AppearanceView(QWidget):
    def __init__(self, vm: AppearanceViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vm = vm

        root = QVBoxLayout(self)
        title = QLabel("Appearance")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(title)

        # Dual-Button: links „Dark“, rechts „Light“
        self._mode_btn = DualChoiceButton(
            "Dark", "Light",
            on_left=self._vm.setDark,
            on_right=self._vm.setLight,
            parent=self
        )
        root.addWidget(self._mode_btn)

        # Initialen Zustand spiegeln
        self._mode_btn.setChecked(self._vm.is_dark)  # True → Dark (links), False → Light (rechts)

        # Wenn sich das Theme extern ändert, Button nachziehen
        self._vm.themeChanged.connect(lambda: self._mode_btn.setChecked(self._vm.is_dark))

        root.addStretch(1)
