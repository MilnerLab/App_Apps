from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from base_core.ipc.worker_handle import WorkerStatus
from base_qt.ui.dirty_indicator import DirtyIndicator
from base_qt.ui.field_draft import FieldDraft
from base_qt.ui.form.specs import AngleSpec
from base_qt.ui.worker_control_widget import WorkerControlWidget
from app_apps.analysis.phase_control.ui.stabilization_control_vm import StabilizationControlVM

if TYPE_CHECKING:
    from app_apps.analysis.phase_control.ui.phase_config_dialog import PhaseConfigDialog


class StabilizationControlView(QWidget):
    def __init__(
        self,
        vm: StabilizationControlVM,
        config_dialog: PhaseConfigDialog,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._vm = vm

        self._phase_draft: FieldDraft[float] = FieldDraft(vm.config.set_phase.Deg)
        self._fitall_draft: FieldDraft[bool] = FieldDraft(vm.config.fit_all_params)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        # --- Framed settings block ---
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setFrameShadow(QFrame.Shadow.Plain)
        fbox = QVBoxLayout(frame)
        fbox.setContentsMargins(6, 4, 6, 4)
        fbox.setSpacing(3)

        phase_spec = AngleSpec("Set phase")
        self._phase_widget = phase_spec.create_widget()
        phase_spec.set_value(self._phase_widget, vm.config.set_phase)

        phase_row = QHBoxLayout()
        phase_row.setSpacing(4)
        self._phase_ind = DirtyIndicator()
        phase_row.addWidget(self._phase_ind)
        phase_row.addWidget(QLabel("Set phase"))
        phase_row.addWidget(self._phase_widget)
        fbox.addLayout(phase_row)

        fitall_row = QHBoxLayout()
        fitall_row.setSpacing(4)
        self._fitall_ind = DirtyIndicator()
        fitall_row.addWidget(self._fitall_ind)
        self._fit_all_cb = QCheckBox("Fit all params")
        self._fit_all_cb.setChecked(vm.config.fit_all_params)
        fitall_row.addWidget(self._fit_all_cb)
        fitall_row.addStretch()
        fbox.addLayout(fitall_row)

        row.addWidget(frame)

        # --- Apply and Config buttons ---
        self._apply_btn = QPushButton("Apply")
        row.addWidget(self._apply_btn)
        self._config_btn = QPushButton("Config")
        row.addWidget(self._config_btn)

        # --- Push worker controls to the far right ---
        row.addStretch()
        self._worker_ctrl = WorkerControlWidget(vm.start, vm.pause, vm.reset)
        row.addWidget(self._worker_ctrl)

        self._worker_ctrl.set_status(vm.worker_state)

        # --- Connections ---
        phase_spec.connect_change(
            self._phase_widget,
            lambda: self._phase_draft.set(phase_spec.get_value(self._phase_widget).Deg),
        )
        self._fit_all_cb.checkStateChanged.connect(
            lambda state: self._fitall_draft.set(state == Qt.CheckState.Checked)
        )
        self._phase_draft.dirty_changed.connect(self._phase_ind.set_dirty)
        self._fitall_draft.dirty_changed.connect(self._fitall_ind.set_dirty)

        self._apply_btn.clicked.connect(self._on_apply)
        self._config_btn.clicked.connect(config_dialog.open)
        vm.worker_state_changed.connect(self._on_worker_state_changed)

    def _on_apply(self) -> None:
        self._vm.apply(self._phase_draft.commit(), self._fitall_draft.commit())

    def _on_worker_state_changed(self, status: WorkerStatus) -> None:
        self._worker_ctrl.set_status(status)
