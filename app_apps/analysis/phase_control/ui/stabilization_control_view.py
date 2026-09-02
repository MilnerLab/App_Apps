from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from base_core.ipc.worker_handle import WorkerStatus
from base_qt.ui.dirty_indicator import DirtyIndicator
from base_qt.ui.field_draft import FieldDraft
from base_qt.ui.form.specs import AngleSpec
from base_qt.ui.worker_control_widget import WorkerControlWidget
from app_apps.analysis.phase_control.ui.stabilization_control_view_model import StabilizationControlViewModel

if TYPE_CHECKING:
    from app_apps.analysis.phase_control.ui.phase_config_view import PhaseConfigView


class StabilizationControlView(QWidget):
    def __init__(
        self,
        vm: StabilizationControlViewModel,
        config_dialog: PhaseConfigView,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._vm = vm

        self._phase_draft: FieldDraft[float] = FieldDraft(vm.config.set_phase.Deg)

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

        freq_row = QHBoxLayout()
        freq_row.setSpacing(4)
        self._freq_cb = QCheckBox("Plot in frequency")
        self._freq_cb.setChecked(vm.plot_frequency)
        freq_row.addWidget(self._freq_cb)
        freq_row.addStretch()
        fbox.addLayout(freq_row)

        row.addWidget(frame)
        row.addWidget(self._build_reference_frame())

        # --- Apply and Config buttons ---
        self._apply_btn = QPushButton("Apply")
        row.addWidget(self._apply_btn)
        self._config_btn = QPushButton("Config")
        row.addWidget(self._config_btn)

        # --- Push worker controls to the far right ---
        row.addStretch()
        self._worker_ctrl = WorkerControlWidget(vm.start, vm.pause, vm.resume, vm.stop)
        row.addWidget(self._worker_ctrl)

        self._worker_ctrl.set_status(vm.worker_state)

        # --- Connections ---
        phase_spec.connect_change(
            self._phase_widget,
            lambda: self._phase_draft.set(phase_spec.get_value(self._phase_widget).Deg),
        )
        self._freq_cb.checkStateChanged.connect(
            lambda state: self._vm.set_plot_frequency(state == Qt.CheckState.Checked)
        )
        self._phase_draft.dirty_changed.connect(self._phase_ind.set_dirty)

        self._apply_btn.clicked.connect(self._on_apply)
        self._config_btn.clicked.connect(config_dialog.open)
        vm.worker_state_changed.connect(self._on_worker_state_changed)
        vm.template_state_changed.connect(self._template_label.setText)

    # --- frozen reference -------------------------------------------------
    def _build_reference_frame(self) -> QWidget:
        """Capture / Save / Recall, plus a one-line state readout.

        The readout is not decoration: "capturing 4/10" and "locked" are the difference
        between a loop that is holding and one that is correcting, and the operator has no
        other way to tell them apart from the plot.
        """
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setFrameShadow(QFrame.Shadow.Plain)
        box = QVBoxLayout(frame)
        box.setContentsMargins(6, 4, 6, 4)
        box.setSpacing(3)

        self._template_label = QLabel(self._vm.template_text)
        box.addWidget(self._template_label)

        btns = QHBoxLayout()
        btns.setSpacing(4)
        capture = QPushButton("Capture reference")
        capture.setToolTip("Collect the next 10 consecutively accepted traces, average "
                           "them, and freeze the fitted shape as the phase template")
        capture.clicked.connect(self._vm.capture_reference)
        save = QPushButton("Save")
        save.clicked.connect(self._on_save_reference)
        recall = QPushButton("Recall")
        recall.setToolTip("Load a saved template, overriding the current one")
        recall.clicked.connect(self._on_recall_reference)
        for b in (capture, save, recall):
            btns.addWidget(b)
        btns.addStretch()
        box.addLayout(btns)
        return frame

    def _on_save_reference(self) -> None:
        if not self._vm.has_template:
            QMessageBox.information(self, "Save reference",
                                    "There is no template installed to save.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save phase reference", "phase_reference.json", "JSON (*.json)")
        if not path:
            return
        try:
            self._vm.save_reference(path)
        except OSError as e:
            QMessageBox.warning(self, "Save reference", f"Could not write the file: {e}")

    def _on_recall_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Recall phase reference", "", "JSON (*.json)")
        if not path:
            return
        try:
            self._vm.recall_reference(path)
        except (OSError, ValueError, KeyError, TypeError) as e:
            QMessageBox.warning(self, "Recall reference",
                                f"That file is not a usable phase reference: {e}")

    def _on_apply(self) -> None:
        self._vm.apply(self._phase_draft.commit())

    def _on_worker_state_changed(self, status: WorkerStatus) -> None:
        self._worker_ctrl.set_status(status)
