from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QButtonGroup
from PySide6.QtCore import Qt

from LabviewToPython.designs.buttons.dual_button import DualChoiceButton

from ..viewmodels.units_vm import UnitsViewModel
from LabviewToPython.core.enums.pressure_unit import PressureUnit

# Pfad so lassen, wie dein Ordner heißt:

class UnitsView(QWidget):
    def __init__(self, vm: UnitsViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vm = vm

        root = QVBoxLayout(self)
        title = QLabel("Units"); title.setStyleSheet("font-size:18px; font-weight:600;")
        root.addWidget(title)

        # ----- Length: DualChoiceButton -----
        lbl_len = QLabel("Length"); lbl_len.setStyleSheet("margin-top:8px; font-weight:600;")
        root.addWidget(lbl_len)

        self._len_btn = DualChoiceButton(
            "Metric", "Imperial",
            on_left=self._vm.setMetric,
            on_right=self._vm.setImperial,
            parent=self
        )
        root.addWidget(self._len_btn)

        # initial + updates
        self._len_btn.setChecked(self._vm.is_metric())
        self._vm.lengthChanged.connect(lambda: self._len_btn.setChecked(self._vm.is_metric()))

        # ----- Pressure: Segmented Button Group -----
        lbl_p = QLabel("Pressure"); lbl_p.setStyleSheet("margin-top:12px; font-weight:600;")
        root.addWidget(lbl_p)

        row = QHBoxLayout(); root.addLayout(row)
        self._p_group: QButtonGroup = QButtonGroup(self); self._p_group.setExclusive(True)
        self._p_buttons: dict[PressureUnit, QPushButton] = {}

        def add(unit: PressureUnit, text: str) -> None:
            b = QPushButton(text, self)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, u=unit: self._vm.setPressure(u))
            # kleine Optik wie Segment
            b.setStyleSheet("padding:6px 12px;")
            row.addWidget(b)
            self._p_group.addButton(b)
            self._p_buttons[unit] = b

        add(PressureUnit.PA,   "Pa")
        add(PressureUnit.BAR,  "bar")
        add(PressureUnit.TORR, "torr")
        add(PressureUnit.PSI,  "psi")
        add(PressureUnit.INHG, "inHg")
        row.addStretch(1)

        self._update_pressure_buttons()
        self._vm.pressureChanged.connect(self._update_pressure_buttons)

        root.addStretch(1)

    # ---- helpers ----
    def _update_pressure_buttons(self) -> None:
        current = self._vm.pressure_unit()
        for u, b in self._p_buttons.items():
            b.setChecked(u == current)
