from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from base_qt.ui.form import ConfigForm, FloatSpec

from app_apps.routines.spectrum_soak.ui.view_model import SpectrumSoakViewModel


class SpectrumSoakView(ConfigForm):
    """Routines -> Spectrum Soak.

    Records the spectrometer to one HDF5 file and does nothing else -- it starts no
    stages and does not touch the phase loop. The comparison it exists for is run by
    hand: record once with stabilization off, once with it on, from the Phase Control
    panel. Deliberately not automated into a single button, because "loop on" and
    "loop off" have to be the *only* difference between the two files, and a panel that
    reconfigured the loop between arms would be changing what it is measuring.
    """

    _specs = {
        "duration_s": FloatSpec("Duration", min=1.0, max=86_400.0, decimals=1,
                                step=10.0, suffix="s"),
        "period_s": FloatSpec("Period", min=0.0, max=3600.0, decimals=3,
                              step=0.5, suffix="s"),
    }

    def __init__(self, vm: SpectrumSoakViewModel, parent: QWidget) -> None:
        self._vm = vm
        super().__init__("Spectrum Soak", vm.settings, parent, vm=vm)

        # The spectrometer free-runs; the period decimates its stream rather than asking
        # it for a frame. Said here as well as in the docstring because the difference is
        # invisible in the file and shows up as spacing that never quite matches.
        note = QLabel("Period 0 records every spectrum. Otherwise it is a floor on the "
                      "spacing — the spectrometer free-runs, so recorded spacing lands "
                      "between the period and the period plus one frame time. Read the "
                      "time axis from timestamp_ns.")
        note.setWordWrap(True)
        self.body_layout.addWidget(note)

        out_row = QHBoxLayout()
        out_label = QLabel("Output folder")
        out_label.setMinimumWidth(130)
        self._out_dir_edit = QLineEdit(str(vm.settings.out_dir))
        self._out_dir_edit.setPlaceholderText("folder for the SOAK_*.h5 files")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse)
        out_row.addWidget(out_label)
        out_row.addWidget(self._out_dir_edit, stretch=1)
        out_row.addWidget(browse_btn)
        self.body_layout.addLayout(out_row)

        tag_row = QHBoxLayout()
        tag_label = QLabel("Tag")
        tag_label.setMinimumWidth(130)
        self._tag_edit = QLineEdit(vm.settings.tag)
        self._tag_edit.setPlaceholderText("folded into the filename, e.g. loop_off")
        tag_row.addWidget(tag_label)
        tag_row.addWidget(self._tag_edit, stretch=1)
        self.body_layout.addLayout(tag_row)

        controls = QHBoxLayout()
        self._start_btn = QPushButton("Start recording")
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(vm.stop)
        self._stop_btn.setEnabled(False)
        controls.addWidget(self._start_btn)
        controls.addWidget(self._stop_btn)
        controls.addStretch(1)
        self.body_layout.addLayout(controls)

        self._status = QLabel("idle")
        self._status.setWordWrap(True)
        self.body_layout.addWidget(self._status)

        vm.bind_update(self._render)

    def _on_browse(self) -> None:
        start = self._out_dir_edit.text().strip() or str(Path.cwd())
        chosen = QFileDialog.getExistingDirectory(self, "Soak output folder", start)
        if chosen:
            self._out_dir_edit.setText(str(Path(chosen)))

    def _populate(self) -> None:
        super()._populate()
        # getattr guards: the base class populates during __init__, before these exist.
        if getattr(self, "_out_dir_edit", None) is not None:
            self._out_dir_edit.setText(str(self._config.out_dir))
        if getattr(self, "_tag_edit", None) is not None:
            self._tag_edit.setText(self._config.tag)

    def _apply(self) -> None:
        super()._apply()
        if getattr(self, "_out_dir_edit", None) is not None:
            text = self._out_dir_edit.text().strip()
            # A blank box means "leave it alone" rather than "write to the root": echo the
            # value actually in force back into the widget so the two cannot disagree.
            if text:
                self._config.out_dir = Path(text)
            self._out_dir_edit.setText(str(self._config.out_dir))
        if getattr(self, "_tag_edit", None) is not None:
            self._config.tag = self._tag_edit.text().strip()

    def _on_start(self) -> None:
        # Flush the widgets into the bound settings before the recorder reads them.
        self._apply()
        self._vm.start()

    def _render(self, text: str, running: bool) -> None:
        self._status.setText(text)
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)

    def on_apply(self) -> None:
        pass
