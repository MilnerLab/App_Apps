from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
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
        self._knife_cb = QCheckBox("Knife edge")
        self._knife_cb.setToolTip(
            "Mark where the clip was detected. Samples beyond the marker were excluded "
            "from the fit, so it shows the edge of the data the phase rests on. "
            "Nothing is drawn on an unclipped frame."
        )
        self._knife_cb.setChecked(vm.show_knife_edges)
        freq_row.addWidget(self._knife_cb)

        # Drag the left marker to override where the readout's short-wavelength terminal
        # sits; this hands it back. Enabled only while an override is actually in force, so
        # the button doubles as the indicator that one IS -- otherwise a dragged edge is
        # indistinguishable from a detected one on a glance at the chart.
        self._auto_cut_btn = QPushButton("Auto")
        self._auto_cut_btn.setToolTip(
            "The f_cfg readout quotes its short-wavelength terminal at the FWHM edge, or "
            "at the left knife edge when that is the higher wavelength -- whichever the "
            "data actually reaches. Drag the left marker to override it; press Auto to go "
            "back to the detected cut."
        )
        self._auto_cut_btn.setEnabled(vm.cut_left_is_manual)
        freq_row.addWidget(self._auto_cut_btn)
        freq_row.addStretch()
        fbox.addLayout(freq_row)

        # --- which traces are drawn ---------------------------------------------------
        # Three curves overlap on one chart and each answers a different question: where the
        # fringes are RIGHT NOW, where the loop's average says they are (which is what the
        # correction is computed from), and where they are being held. All on by default --
        # the comparison between them is the point -- but any one of them can be taken off
        # to read the others.
        trace_row = QHBoxLayout()
        trace_row.setSpacing(4)
        trace_row.addWidget(QLabel("Traces:"))
        self._raw_cb = QCheckBox("Raw")
        self._raw_cb.setToolTip("The live spectrometer trace, one frame, uncorrected.")
        self._raw_cb.setChecked(vm.show_raw)
        trace_row.addWidget(self._raw_cb)
        self._mean_cb = QCheckBox("Averaged")
        self._mean_cb.setToolTip(
            "The reference shape moved to the loop's circular running mean -- the trace the "
            "correction is actually computed from. Needs a reference installed."
        )
        self._mean_cb.setChecked(vm.show_mean)
        trace_row.addWidget(self._mean_cb)
        self._target_cb = QCheckBox("Target")
        self._target_cb.setToolTip(
            "The installed reference: red when captured, yellow when recalled from a file."
        )
        self._target_cb.setChecked(vm.show_target)
        trace_row.addWidget(self._target_cb)
        trace_row.addStretch()
        fbox.addLayout(trace_row)

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
        self._auto_cut_btn.clicked.connect(self._vm.clear_manual_cut_left)
        self._vm.cut_left_changed.connect(
            lambda _nm, manual: self._auto_cut_btn.setEnabled(bool(manual)))
        self._raw_cb.checkStateChanged.connect(
            lambda state: self._vm.set_show_raw(state == Qt.CheckState.Checked)
        )
        self._mean_cb.checkStateChanged.connect(
            lambda state: self._vm.set_show_mean(state == Qt.CheckState.Checked)
        )
        self._target_cb.checkStateChanged.connect(
            lambda state: self._vm.set_show_target(state == Qt.CheckState.Checked)
        )
        self._knife_cb.checkStateChanged.connect(
            lambda state: self._vm.set_show_knife_edges(state == Qt.CheckState.Checked)
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

        # Where the plate is, what it was last told to do, and when it will be told again.
        self._plate_label = QLabel(self._vm.plate_text)
        self._plate_label.setToolTip(
            "The RGV waveplate's current orientation, the rotation the loop last commanded "
            "(or why it held), and the time until the next correction instant")
        self._vm.plate_status_changed.connect(self._plate_label.setText)
        box.addWidget(self._plate_label)

        # Which loop runs. Radio buttons rather than a checkbox: both options are named,
        # so the one that is NOT selected is still readable off the panel -- and the two
        # differ by more than a rate, so "not slow" is not a useful way to describe fast.
        mode = QHBoxLayout()
        mode.setSpacing(4)
        self._slow_radio = QRadioButton("Slow")
        self._slow_radio.setToolTip(
            "Frozen-template loop: capture a shape, then correct once every "
            "correction period from the averaged phase. Accurate, and the default.")
        self._fast_radio = QRadioButton("Fast")
        self._fast_radio.setToolTip(
            "Cold per-frame loop: a full fit and a correction on every accepted trace. "
            "Responsive but noisier — for pulling a badly drifted setup back into range.")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._slow_radio)
        self._mode_group.addButton(self._fast_radio)
        (self._slow_radio if self._vm.slow_correction else self._fast_radio).setChecked(True)
        self._slow_radio.toggled.connect(self._vm.set_slow_correction)
        mode.addWidget(QLabel("Correction:"))
        mode.addWidget(self._slow_radio)
        mode.addWidget(self._fast_radio)
        mode.addStretch()
        box.addLayout(mode)

        btns = QHBoxLayout()
        btns.setSpacing(4)
        capture = QPushButton("Capture reference")
        capture.setToolTip("Collect the next 10 consecutively accepted traces, average "
                           "them, and freeze the fitted shape as the phase template")
        capture.clicked.connect(self._vm.capture_reference)
        save = QPushButton("Save")
        save.clicked.connect(self._on_save_reference)
        recall = QPushButton("Recall")
        recall.setToolTip("Load a saved template and hold it: it is drawn on the chart at "
                          "once, and nothing automatic replaces it until it is cleared")
        recall.clicked.connect(self._on_recall_reference)
        # Deselecting the recall is the ONLY way back to automatic capture short of pressing
        # Capture reference, so it needs a control of its own -- enabled only when there is
        # actually a recalled template to clear.
        self._clear_recall_btn = QPushButton("Clear recall")
        self._clear_recall_btn.setToolTip(
            "Stop holding the recalled template and let the loop capture its own again")
        self._clear_recall_btn.setEnabled(self._vm.has_recall)
        self._clear_recall_btn.clicked.connect(self._vm.clear_recall)
        self._vm.recall_changed.connect(self._clear_recall_btn.setEnabled)
        for b in (capture, save, recall, self._clear_recall_btn):
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
