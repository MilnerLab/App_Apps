from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from base_qt.ui.panel import Panel
from .rotator_vm import RotatorVM


class RotatorPanel(Panel):
    """
    Elliptec rotator status and manual control.

    Shows the current absolute angle and provides jog +/- buttons
    and a Home button.  Step size is editable.
    """

    def __init__(self, vm: RotatorVM) -> None:
        super().__init__("Rotator", vm)

    @property
    def vm(self) -> RotatorVM:
        return super().vm  # type: ignore[return-value]

    def setup(self) -> None:
        # Angle readout
        self._angle_label = QLabel("—")
        self._angle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._angle_label.setStyleSheet("font-size: 32px; font-weight: 700;")
        self.body_layout.addWidget(self._angle_label)

        # Step size control
        step_widget = QWidget()
        step_form = QFormLayout(step_widget)
        step_form.setContentsMargins(0, 4, 0, 0)
        self._step_spin = QDoubleSpinBox()
        self._step_spin.setRange(0.01, 90.0)
        self._step_spin.setValue(1.0)
        self._step_spin.setDecimals(2)
        self._step_spin.setSuffix("°")
        step_form.addRow("Step size", self._step_spin)
        self.body_layout.addWidget(step_widget)

        # Jog buttons + Home
        btn_row = QWidget()
        row = QHBoxLayout(btn_row)
        row.setContentsMargins(0, 0, 0, 0)

        self._jog_minus = QPushButton("−")
        self._jog_plus  = QPushButton("+")
        self._home_btn  = QPushButton("Home")

        self._jog_minus.clicked.connect(self._on_jog_minus)
        self._jog_plus.clicked.connect(self._on_jog_plus)
        self._home_btn.clicked.connect(self.vm.home)

        for btn in (self._jog_minus, self._jog_plus, self._home_btn):
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.addWidget(btn)

        self.body_layout.addWidget(btn_row)
        self.body_layout.addStretch(1)

        self._connect(self.vm.angle_updated, self._on_angle_updated)

    def _on_angle_updated(self, deg: float) -> None:
        self._angle_label.setText(f"{deg:.3f}°")

    def _on_jog_minus(self) -> None:
        self.vm.rotate(-self._step_spin.value())

    def _on_jog_plus(self) -> None:
        self.vm.rotate(self._step_spin.value())
