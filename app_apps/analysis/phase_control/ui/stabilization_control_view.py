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

        # The four traces the panel draws, and a switch for each. "Fit" is the frozen shape
        # sitting at the phase this frame measured; "Target" is the same shape at the
        # setpoint. The gap between them IS the error the loop corrects, so being able to
        # isolate either against the raw spectrum is how a bad lock is read. "Avg" is the
        # mean of the raw frames in the current block -- the thing the loop actually
        # corrects on. Fit starts OFF: it is per-frame and the loop never acts on one frame.
        trace_row = QHBoxLayout()
        trace_row.setSpacing(4)
        trace_row.addWidget(QLabel("Traces:"))
        self._raw_cb = QCheckBox("Raw")
        self._raw_cb.setToolTip("The spectrometer trace, as measured")
        self._fit_cb = QCheckBox("Fit")
        self._fit_cb.setToolTip(
            "The frozen reference shape at the phase this frame measured — what the loop "
            "believes the light is doing")
        self._target_cb = QCheckBox("Target")
        self._target_cb.setToolTip(
            "The same frozen shape at the set phase — where the loop is holding it")
        self._avg_cb = QCheckBox("Avg")
        self._avg_cb.setToolTip(
            "The running mean of the raw frames collected into the current averaging block "
            "— the trace the correction is actually computed from. It restarts every "
            "time the block does, so it never smears across a plate move.")
        for cb, on in ((self._raw_cb, vm.show_raw), (self._fit_cb, vm.show_fit),
                       (self._target_cb, vm.show_target), (self._avg_cb, vm.show_avg)):
            cb.setChecked(on)
            trace_row.addWidget(cb)
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
        self._knife_cb.checkStateChanged.connect(
            lambda state: self._vm.set_show_knife_edges(state == Qt.CheckState.Checked)
        )
        self._raw_cb.checkStateChanged.connect(
            lambda state: self._vm.set_show_raw(state == Qt.CheckState.Checked)
        )
        self._fit_cb.checkStateChanged.connect(
            lambda state: self._vm.set_show_fit(state == Qt.CheckState.Checked)
        )
        self._target_cb.checkStateChanged.connect(
            lambda state: self._vm.set_show_target(state == Qt.CheckState.Checked)
        )
        self._avg_cb.checkStateChanged.connect(
            lambda state: self._vm.set_show_avg(state == Qt.CheckState.Checked)
        )
        vm.readout_changed.connect(self._refresh_readouts)
        self._phase_draft.dirty_changed.connect(self._phase_ind.set_dirty)

        self._apply_btn.clicked.connect(self._on_apply)
        self._config_btn.clicked.connect(config_dialog.open)
        vm.worker_state_changed.connect(self._on_worker_state_changed)
        vm.loop_state_changed.connect(self._state_label.setText)

    # --- reference --------------------------------------------------------
    def _build_reference_frame(self) -> QWidget:
        """Capture target, plus a one-line state readout.

        The readout is not decoration: "capturing 4/10" and "averaging 7/10" are the
        difference between a loop that is holding and one that is about to move the plate,
        and the operator has no other way to tell them apart from the chart.
        """
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setFrameShadow(QFrame.Shadow.Plain)
        box = QVBoxLayout(frame)
        box.setContentsMargins(6, 4, 6, 4)
        box.setSpacing(3)

        self._state_label = QLabel(self._vm.loop_text)
        box.addWidget(self._state_label)

        btns = QHBoxLayout()
        btns.setSpacing(4)
        capture = QPushButton("Capture target")
        capture.setToolTip(
            "Refit every parameter cold over the next 10 consecutively accepted traces, "
            "freeze the fitted shape, and take the phase it measures as the new set phase. "
            "This is how the loop is re-referenced after the centrifuge changes — the "
            "error goes to zero and the loop holds the fringes where they are now.")
        capture.clicked.connect(self._vm.capture_target)
        btns.addWidget(capture)
        # Where the plate is, what it was last told to do, and how far off the next
        # correction is. Restored from the legacy panel: without them a held loop and a
        # stalled one look identical, and a correction that was commanded but not executed
        # (a spin in progress, a controller fault) is invisible.
        self._plate_label = QLabel()
        self._plate_label.setToolTip("Absolute half-wave-plate angle, as read back")
        self._corr_label = QLabel()
        self._corr_label.setToolTip(
            "The last increment this loop commanded. Signed: the direction is the sign.")
        self._next_label = QLabel()
        self._next_label.setToolTip(
            "Frames still to collect before the block fills and a correction can be issued")
        readouts = QHBoxLayout()
        readouts.setSpacing(10)
        for lab in (self._plate_label, self._corr_label, self._next_label):
            readouts.addWidget(lab)
        readouts.addStretch()
        box.addLayout(readouts)
        self._refresh_readouts()

        btns.addStretch()
        box.addLayout(btns)
        return frame

    def _refresh_readouts(self) -> None:
        # A dash, not 0.00: zero is a legitimate plate angle and a legitimate correction, so
        # rendering "unknown" as a number would be a lie about a reading that has not
        # happened.
        plate = self._vm.waveplate_deg
        corr = self._vm.last_correction_deg
        self._plate_label.setText(
            "Plate: —" if plate is None else f"Plate: {plate:.2f}°")
        self._corr_label.setText(
            "Last: —" if corr is None else f"Last: {corr:+.3f}°")
        self._next_label.setText(self._vm.countdown_text)

    def _on_apply(self) -> None:
        self._vm.apply(self._phase_draft.commit())

    def _on_worker_state_changed(self, status: WorkerStatus) -> None:
        self._worker_ctrl.set_status(status)
