from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app_apps.recording.ui.view_model import SpectrumRecordingViewModel


class SpectrumRecordingControls(QWidget):
    """A "Record" box (output folder, name, file, counters) with the Record/Stop button
    centered below it. A plain widget, embedded in ``SpectrometerControls`` so it appears
    in both the spectrometer popout and its block on the Devices page."""

    def __init__(self, vm: SpectrumRecordingViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vm = vm

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        box = QGroupBox("Record")
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(4)

        out_row = QHBoxLayout()
        out_label = QLabel("Output folder")
        out_label.setMinimumWidth(130)
        self._out_dir_edit = QLineEdit(str(Path.cwd()))
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._on_browse)
        out_row.addWidget(out_label)
        out_row.addWidget(self._out_dir_edit, stretch=1)
        out_row.addWidget(self._browse_btn)
        box_layout.addLayout(out_row)

        name_row = QHBoxLayout()
        name_label = QLabel("Name")
        name_label.setMinimumWidth(130)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("tag for the filename, e.g. alignment_1")
        name_row.addWidget(name_label)
        name_row.addWidget(self._name_edit, stretch=1)
        box_layout.addLayout(name_row)

        self._path_label = QLabel()
        self._path_label.setWordWrap(True)
        self._counts_label = QLabel()
        box_layout.addWidget(self._path_label)
        box_layout.addWidget(self._counts_label)
        layout.addWidget(box)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._record_btn = QPushButton()
        self._record_btn.clicked.connect(self._on_record_clicked)
        btn_row.addWidget(self._record_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # Counters live on the recorder and change on every spectrum; polling them at 1 Hz
        # is cheaper than an event per frame and plenty for a readout.
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh_counts)

        vm.state_changed.connect(self._refresh)
        self._refresh()

    def _on_browse(self) -> None:
        start = self._out_dir_edit.text().strip() or str(Path.cwd())
        chosen = QFileDialog.getExistingDirectory(self, "Recording output folder", start)
        if chosen:
            self._out_dir_edit.setText(str(Path(chosen)))

    def _on_record_clicked(self) -> None:
        if self._vm.is_recording:
            self._vm.stop()
        else:
            self._vm.start(self._out_dir_edit.text().strip(), self._name_edit.text())

    def _refresh(self) -> None:
        recording = self._vm.is_recording
        self._record_btn.setText("Stop recording" if recording else "Record")
        self._out_dir_edit.setEnabled(not recording)
        self._browse_btn.setEnabled(not recording)
        self._name_edit.setEnabled(not recording)
        path = self._vm.path
        if path is None:
            self._path_label.setText("Not recording")
        else:
            self._path_label.setText(f"{'Recording to' if recording else 'Last file'}: {path}")
        if recording:
            self._timer.start()
        else:
            self._timer.stop()
        self._refresh_counts()

    def _refresh_counts(self) -> None:
        traces, metadata, dropped = self._vm.counters()
        self._counts_label.setText(
            f"Traces: {traces}   Metadata entries: {metadata}   Dropped: {dropped}")
