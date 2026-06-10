from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from base_qt.ui.panel import Panel
from app_apps.analysis.phase_control.domain.mode import ControlMode
from .phase_status_vm import PhaseStatusVM


class PhaseStatusPanel(Panel):
    """
    Phase control status and runtime controls.

    Displays current phase and last correction angle.
    Allows switching between PHASE_TRACKING and ENVELOPE mode,
    pausing the active worker, resetting the algorithm, and
    opening the config dialog.
    """

    def __init__(self, vm: PhaseStatusVM) -> None:
        super().__init__("Phase Control", vm)

    @property
    def vm(self) -> PhaseStatusVM:
        return self.__dict__["vm"]  # type: ignore[return-value]

    @vm.setter
    def vm(self, value: PhaseStatusVM) -> None:
        self.__dict__["vm"] = value

    def setup(self) -> None:
        # Phase readout
        self._phase_label = QLabel("—")
        self._phase_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._phase_label.setStyleSheet("font-size: 36px; font-weight: 700;")
        self.body_layout.addWidget(self._phase_label)

        self._correction_label = QLabel("Correction: —")
        self._correction_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._correction_label.setProperty("role", "muted")
        self.body_layout.addWidget(self._correction_label)

        # Mode selector
        self._mode_combo = QComboBox()
        for mode in ControlMode:
            self._mode_combo.addItem(mode.value.replace("_", " ").title(), userData=mode)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.body_layout.addWidget(self._mode_combo)

        # Buttons row
        btn_row = QWidget()
        row_layout = QHBoxLayout(btn_row)
        row_layout.setContentsMargins(0, 0, 0, 0)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setCheckable(True)
        self._pause_btn.clicked.connect(self.vm.toggle_pause)

        self._reset_btn = QPushButton("Reset")
        self._reset_btn.clicked.connect(self.vm.reset)

        self._config_btn = QPushButton("Configure…")
        self._config_btn.clicked.connect(self._open_config)

        for btn in (self._pause_btn, self._reset_btn, self._config_btn):
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row_layout.addWidget(btn)

        self.body_layout.addWidget(btn_row)
        self.body_layout.addStretch(1)

        # Wire VM signals
        self._connect(self.vm.phase_updated,  self._on_phase_updated)
        self._connect(self.vm.paused_changed, self._on_paused_changed)

    def _on_phase_updated(self, phase_deg: float, correction_deg: float) -> None:
        self._phase_label.setText(f"{phase_deg:+.2f}°")
        self._correction_label.setText(f"Correction: {correction_deg:+.3f}°")

    def _on_paused_changed(self, paused: bool) -> None:
        self._pause_btn.setChecked(paused)
        self._pause_btn.setText("Resume" if paused else "Pause")

    def _on_mode_changed(self, _index: int) -> None:
        mode: ControlMode = self._mode_combo.currentData()
        self.vm.set_mode(mode)

    def _open_config(self) -> None:
        from app_apps.analysis.phase_control.ui.phase_config_dialog import PhaseConfigDialog
        dlg = PhaseConfigDialog(self.vm._svc, parent=self)
        dlg.exec()
