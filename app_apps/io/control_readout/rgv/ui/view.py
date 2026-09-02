from __future__ import annotations

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from base_qt.ui.panel_view import PanelView

from app_apps.io.control_readout.ui.motion_controls import MotionControls
from app_apps.io.control_readout.rgv.ui.view_model import (
    DEFAULT_SPIN_HZ,
    MAX_SPIN_HZ,
    MIN_SPIN_HZ,
    RgvViewModel,
)

TITLE = "RGV100BL HWP"


class RgvControls(MotionControls):
    """``MotionControls`` plus continuous rotation, and the two interlocks that go with it.

    Three things can drive this plate, and they have a strict precedence: a manual command
    beats a spin, and a spin beats stabilization. Each step down that order is confirmed by
    its own dialog and, on Yes, STOPS the thing it is overriding before issuing anything --
    so at no point are two of them commanding the plate at once.

    Every move -- relative, absolute, home or spin -- funnels through ``_confirm_move`` and
    ``_confirm_spin_override`` for exactly that reason.
    """

    def __init__(self, vm: RgvViewModel, parent: QWidget | None = None) -> None:
        super().__init__(TITLE, vm, parent)
        self._rgv_vm = vm
        # Installed rather than subclassed into the view model, so MotionControls and
        # MotionViewModel stay free of any knowledge of phase control -- the other four
        # devices have no interlock and need none.
        vm.confirm_move = self._confirm_move
        vm.confirm_spin_override = self._confirm_spin_override
        self.layout().addLayout(self._build_spin_row())
        vm.spin_state_changed.connect(self._render_spin)
        self._render_spin(vm.spinning, DEFAULT_SPIN_HZ)

    # -- continuous rotation ----------------------------------------------------------
    def _build_spin_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self._rate = QDoubleSpinBox()
        self._rate.setDecimals(2)
        self._rate.setRange(MIN_SPIN_HZ, MAX_SPIN_HZ)
        self._rate.setSingleStep(0.1)
        self._rate.setValue(DEFAULT_SPIN_HZ)
        self._rate.setKeyboardTracking(False)
        self._rate.setToolTip(
            f"Mechanical revolutions per second, {MIN_SPIN_HZ}–{MAX_SPIN_HZ} "
            f"({MAX_SPIN_HZ * 360:.0f} deg/s is the RGV100's maximum). On a half-wave "
            f"plate the optical phase modulates at four times this rate. Changing it while "
            f"spinning re-rates without stopping."
        )
        # Live re-rate: the operator is usually hunting for a rate by ear/eye against the
        # signal, and having to stop and restart to try 0.7 instead of 0.6 loses the thread.
        self._rate.valueChanged.connect(self._rgv_vm.set_spin_rate)

        self._spin_btn = QPushButton("Spin")
        self._spin_btn.setToolTip("Rotate continuously. Stops stabilization first.")
        self._spin_btn.clicked.connect(self._toggle_spin)

        self._spin_state = QLabel("")
        self._spin_state.setStyleSheet("font-weight: bold;")

        row.addWidget(QLabel("Continuous"))
        row.addWidget(self._rate)
        row.addWidget(QLabel("rev/s"))
        row.addWidget(self._spin_btn)
        row.addWidget(self._spin_state)
        row.addStretch(1)
        return row

    def _toggle_spin(self) -> None:
        if self._rgv_vm.spinning:
            self._rgv_vm.stop_spin()
        else:
            self._rgv_vm.start_spin(self._rate.value())

    def _render_spin(self, spinning: bool, rev_per_s: float) -> None:
        self._spin_btn.setText("Stop spin" if spinning else "Spin")
        self._spin_state.setText(
            f"spinning — {rev_per_s:.2f} rev/s ({4 * rev_per_s:.1f} Hz phase)"
            if spinning else ""
        )

    def _confirm_spin_override(self, description: str) -> bool:
        answer = QMessageBox.question(
            self,
            "The RGV is spinning",
            f"The plate is rotating continuously.  Stop the spin and {description}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_move(self, description: str) -> bool:
        if not self._rgv_vm.stabilization_running:
            return True
        answer = QMessageBox.question(
            self,
            "Stabilization is running",
            f"The phase loop is driving this plate.\n\n"
            f"Stop stabilization and {description}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._rgv_vm.stop_stabilization()
        return True


class RgvView(PanelView):
    def __init__(self, vm: RgvViewModel, parent: QWidget) -> None:
        super().__init__(TITLE, parent, vm=vm)
        self._vm = vm
        self.body_layout.addWidget(RgvControls(vm, self))
