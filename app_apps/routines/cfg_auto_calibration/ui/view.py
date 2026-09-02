"""The CFG auto-calibration panel.

Three sections:
  * Target sweep — two frequency fields + a Start/End <-> Center/Bandwidth toggle, and the
    Send-to / Recompute actions.
  * Manual adjustment — one control group per stage (grating / delay / probe): a live
    position field that doubles as the absolute-move input, a Home button, and
    ``<< < > >>`` relative jog.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from base_core.ipc.worker_handle import WorkerStatus
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Frequency
from base_qt.ui.controls.quantity_controls import FrequencyControl
from base_qt.ui.panel_view import PanelView
from base_qt.ui.toggle_switch import ToggleSwitch
from base_qt.ui.worker_control_widget import WorkerControlWidget

from app_apps.routines.cfg_auto_calibration.arms import ARM_SPECS, Arm
from app_apps.routines.cfg_auto_calibration.target import CfgTarget, TargetMode
from app_apps.routines.cfg_auto_calibration.ui.view_model import CfgAutoCalibrationViewModel

_ALLOWED_PREFIXES = [Prefix.MEGA, Prefix.GIGA, Prefix.TERA]
_FREQ_MIN_HZ = -1e15
_FREQ_MAX_HZ = 1e15

_LABELS = {
    TargetMode.START_END: ("Start frequency", "End frequency"),
    TargetMode.CENTER_BANDWIDTH: ("Central frequency", "Bandwidth"),
}


class CfgAutoCalibrationView(PanelView):
    def __init__(self, vm: CfgAutoCalibrationViewModel, parent: QWidget) -> None:
        super().__init__("CFG Auto-Calibration", parent, vm=vm)
        self._vm = vm
        self._mode = TargetMode.CENTER_BANDWIDTH
        self._pos_spin: dict[Arm, QDoubleSpinBox] = {}
        self._worker_ctrl: dict[Arm, WorkerControlWidget] = {}

        self.body_layout.addWidget(self._build_target_group())
        self.body_layout.addWidget(self._build_manual_group())
        self.body_layout.addStretch(1)

        vm.position_changed.connect(self._on_position)
        vm.state_changed.connect(self._on_state)
        vm.solution_ready.connect(self._on_solution)
        vm.fit_updated.connect(self._on_fit_updated)

        vm.request_positions()

    # ---------------------------------------------------------------- target
    def _build_target_group(self) -> QGroupBox:
        box = QGroupBox("Target sweep")
        v = QVBoxLayout(box)
        v.setSpacing(6)

        toggle_row = QHBoxLayout()
        toggle_row.addWidget(QLabel("Enter as"))
        self._toggle = ToggleSwitch(text_on="Center / BW", text_off="Start / End")
        self._toggle.setMinimumWidth(120)
        self._toggle.setChecked(self._mode is TargetMode.CENTER_BANDWIDTH)
        self._toggle.toggled.connect(self._on_toggle)
        toggle_row.addWidget(self._toggle)
        toggle_row.addStretch(1)
        v.addLayout(toggle_row)

        self._label_a = QLabel()
        self._label_b = QLabel()
        self._field_a = FrequencyControl(Prefix.TERA, _ALLOWED_PREFIXES, _FREQ_MIN_HZ, _FREQ_MAX_HZ)
        self._field_b = FrequencyControl(Prefix.TERA, _ALLOWED_PREFIXES, _FREQ_MIN_HZ, _FREQ_MAX_HZ)
        for lbl, field in ((self._label_a, self._field_a), (self._label_b, self._field_b)):
            row = QHBoxLayout()
            lbl.setMinimumWidth(130)
            row.addWidget(lbl)
            row.addWidget(field, stretch=1)
            v.addLayout(row)
        self._relabel()

        actions = QHBoxLayout()
        send_btn = QPushButton("Send to")
        send_btn.clicked.connect(self._on_send_to)
        recompute_btn = QPushButton("Recompute…")
        recompute_btn.clicked.connect(self._on_recompute)
        actions.addWidget(send_btn)
        actions.addWidget(recompute_btn)
        actions.addStretch(1)
        v.addLayout(actions)

        self._solution_label = QLabel("")
        self._solution_label.setWordWrap(True)
        v.addWidget(self._solution_label)

        return box

    def _relabel(self) -> None:
        a, b = _LABELS[self._mode]
        self._label_a.setText(a)
        self._label_b.setText(b)

    def _current_target(self) -> CfgTarget:
        return CfgTarget.from_fields(
            self._mode,
            float(self._field_a.get_frequency()),
            float(self._field_b.get_frequency()),
        )

    def _on_toggle(self, checked: bool) -> None:
        target = self._current_target()
        self._mode = TargetMode.CENTER_BANDWIDTH if checked else TargetMode.START_END
        a, b = target.fields(self._mode)
        self._field_a.set_frequency(Frequency(a))
        self._field_b.set_frequency(Frequency(b))
        self._relabel()

    def _on_send_to(self) -> None:
        target = self._current_target()
        self._vm.send_to(target.center_hz, target.bandwidth_hz)

    def _on_recompute(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select xcorr calibration dataset",
            "",
            "Calibration data (*.json *.csv);;All files (*)",
        )
        if path:
            self._vm.recompute(path)

    def _on_solution(self, grating_mm: float, delay_mm: float) -> None:
        self._solution_label.setText(
            f"Solution: grating {grating_mm:.4f} mm, delay {delay_mm:.4f} mm"
        )

    def _on_fit_updated(self, n_points: int, rms_f0_hz: float, rms_df_hz: float) -> None:
        self._solution_label.setText(
            f"Calibration refreshed from {n_points} points "
            f"(RMS f0 {rms_f0_hz:.3e} Hz, Δf {rms_df_hz:.3e} Hz)."
        )

    # ---------------------------------------------------------------- manual
    def _build_manual_group(self) -> QGroupBox:
        box = QGroupBox("Manual adjustment")
        v = QVBoxLayout(box)
        v.setSpacing(8)
        for arm in (Arm.GRATING, Arm.DELAY, Arm.PROBE):
            v.addWidget(self._build_stage_group(arm))
        return box

    def _build_stage_group(self, arm: Arm) -> QGroupBox:
        spec = ARM_SPECS[arm]
        box = QGroupBox(f"{spec.label}  ·  {spec.stage} (axis {spec.axis})")
        v = QVBoxLayout(box)
        v.setSpacing(4)

        # Worker lifecycle row.
        ctrl = WorkerControlWidget(
            lambda: self._vm.start(arm),
            lambda: self._vm.pause(arm),
            lambda: self._vm.resume(arm),
            lambda: self._vm.stop(arm),
            parent=self,
        )
        ctrl.set_status(self._vm.worker_status(arm))
        self._worker_ctrl[arm] = ctrl
        head = QHBoxLayout()
        head.addWidget(ctrl)
        head.addStretch(1)
        v.addLayout(head)

        # Motion row: << < [pos mm] Go > >> Home
        row = QHBoxLayout()
        row.setSpacing(3)

        far_back = QPushButton("<<")
        back = QPushButton("<")
        fwd = QPushButton(">")
        far_fwd = QPushButton(">>")
        for b in (far_back, back, fwd, far_fwd):
            b.setFixedWidth(32)
        far_back.setToolTip(f"−{spec.step_coarse_mm} mm")
        back.setToolTip(f"−{spec.step_fine_mm} mm")
        fwd.setToolTip(f"+{spec.step_fine_mm} mm")
        far_fwd.setToolTip(f"+{spec.step_coarse_mm} mm")
        far_back.clicked.connect(lambda: self._vm.jog(arm, -spec.step_coarse_mm))
        back.clicked.connect(lambda: self._vm.jog(arm, -spec.step_fine_mm))
        fwd.clicked.connect(lambda: self._vm.jog(arm, spec.step_fine_mm))
        far_fwd.clicked.connect(lambda: self._vm.jog(arm, spec.step_coarse_mm))

        spin = QDoubleSpinBox()
        spin.setRange(spec.limit_min_mm, spec.limit_max_mm)
        spin.setDecimals(4)
        spin.setSingleStep(spec.step_fine_mm)
        spin.setSuffix(" mm")
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._pos_spin[arm] = spin

        go = QPushButton("Go")
        go.setToolTip("Absolute move to the value shown")
        go.clicked.connect(lambda: self._vm.move_absolute(arm, spin.value()))

        home = QPushButton("Home")
        home.clicked.connect(lambda: self._vm.home(arm))

        row.addWidget(far_back)
        row.addWidget(back)
        row.addWidget(spin, stretch=1)
        row.addWidget(go)
        row.addWidget(fwd)
        row.addWidget(far_fwd)
        row.addSpacing(6)
        row.addWidget(home)
        v.addLayout(row)

        return box

    def _on_position(self, arm: Arm, position_mm: float) -> None:
        spin = self._pos_spin.get(arm)
        if spin is None:
            return
        # Don't clobber a value the operator is mid-edit; the field doubles as the
        # absolute-move input, so only track the live position when it isn't focused.
        if not spin.hasFocus():
            spin.blockSignals(True)
            spin.setValue(position_mm)
            spin.blockSignals(False)

    def _on_state(self, arm: Arm, status: WorkerStatus) -> None:
        ctrl = self._worker_ctrl.get(arm)
        if ctrl is not None:
            ctrl.set_status(status)
