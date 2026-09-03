from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from base_qt.ui.form import ConfigForm, FloatSpec

from app_apps.routines.spectrum_soak.loader import SoakLoadError, load_soak
from app_apps.routines.spectrum_soak.ui.view_model import SpectrumSoakViewModel

#: Rows held in the *image*. The file keeps everything; this only bounds what is drawn,
#: so a long soak at period 0 cannot grow the panel's memory without limit. At the far
#: end the oldest rows scroll off the top -- which is the right trade for a display whose
#: whole job is "has it moved recently?", and it is why the file is the record, not this.
_MAX_ROWS = 4000


class SpectrumSoakView(ConfigForm):
    """Routines -> Spectrum Soak.

    Records the spectrometer to one HDF5 file and draws it accumulating as a waterfall:
    wavelength across, time down, counts as colour. A drifting fringe pattern reads as
    slanted stripes and a held one as straight vertical bars, which is the entire question
    this panel exists to answer -- and it is answerable at a glance, long before the file
    is analysed.

    It starts no stages and does not touch the phase loop. The comparison it is for is run
    by hand: record once with stabilization off, once with it on, from the Phase Control
    panel. Deliberately not automated into one button, because "loop on" and "loop off"
    have to be the *only* difference between the two files, and a panel that reconfigured
    the loop between arms would be changing what it is measuring.
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
                      "time axis from timestamp_ns. With stabilization RUNNING and an ROI "
                      "set in the Phase Control panel, only that ROI is recorded.")
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
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._on_pause)
        self._pause_btn.setEnabled(False)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(vm.stop)
        self._stop_btn.setEnabled(False)
        # Loading draws into the same heatmap, which is the point -- an old run and a new
        # one are read the same way. It is disabled while recording rather than allowed to
        # overwrite the live picture with a file.
        self._load_btn = QPushButton("Load…")
        self._load_btn.setToolTip("Open a SOAK_*.h5 and draw it here. Recording is "
                                 "unaffected; this only replaces what is on the chart.")
        self._load_btn.clicked.connect(self._on_load)
        controls.addWidget(self._start_btn)
        controls.addWidget(self._pause_btn)
        controls.addWidget(self._stop_btn)
        controls.addWidget(self._load_btn)
        controls.addStretch(1)
        self.body_layout.addLayout(controls)

        self._status = QLabel("idle")
        self._status.setWordWrap(True)
        self.body_layout.addWidget(self._status)

        self._build_heatmap()

        #: Local mirror of the pause toggle -- the button drives its own label, and the
        #: view model resets it through _render whenever a run ends.
        self._paused = False

        vm.bind_update(self._render)
        vm.bind_data(self._on_data)

    # -- the waterfall ----------------------------------------------------

    def _build_heatmap(self) -> None:
        self._plot = pg.PlotWidget()
        self._plot.setMinimumHeight(260)
        self._plot.setLabel("bottom", "Wavelength", units="nm")
        self._plot.setLabel("left", "Spectrum #")
        # Newest at the bottom, so the picture grows downward the way the run does.
        self._plot.getViewBox().invertY(True)
        # row-major: the array is indexed [time, pixel], which is also how it is stored
        # and how it reads. The default (col-major) would need every block transposed.
        self._image = pg.ImageItem(axisOrder="row-major")
        self._image.setColorMap(pg.colormap.get("viridis"))
        self._plot.addItem(self._image)
        self.body_layout.addWidget(self._plot, stretch=1)

        #: Growing buffer, doubled rather than re-allocated per row: at period 0 this
        #: takes a row several times a second and vstack-per-frame would spend the whole
        #: soak copying.
        self._buf: np.ndarray | None = None
        self._n_rows = 0
        self._wl: np.ndarray | None = None

    def _reset_heatmap(self) -> None:
        self._buf = None
        self._n_rows = 0
        self._wl = None
        self._image.clear()

    def _on_data(self, wl: np.ndarray, block: np.ndarray) -> None:
        if block.ndim != 2 or block.size == 0:
            return
        n_px = block.shape[1]
        if self._buf is None or self._buf.shape[1] != n_px:
            # First block, or the axis changed under us (a re-grating mid-soak). Start
            # over rather than draw two different axes on one picture.
            self._buf = np.empty((max(64, block.shape[0]), n_px), dtype=np.float32)
            self._n_rows = 0
        self._wl = wl

        for row in block:
            if self._n_rows == self._buf.shape[0]:
                if self._buf.shape[0] >= _MAX_ROWS:
                    # Full: scroll. Halving keeps this to one copy per _MAX_ROWS/2 rows
                    # rather than one per row.
                    keep = _MAX_ROWS // 2
                    self._buf[:keep] = self._buf[-keep:]
                    self._n_rows = keep
                else:
                    grown = np.empty((min(_MAX_ROWS, self._buf.shape[0] * 2), n_px),
                                     dtype=np.float32)
                    grown[:self._n_rows] = self._buf[:self._n_rows]
                    self._buf = grown
            self._buf[self._n_rows] = row
            self._n_rows += 1

        view = self._buf[:self._n_rows]
        finite = view[np.isfinite(view)]
        if finite.size == 0:
            return
        lo, hi = float(finite.min()), float(finite.max())
        if hi <= lo:
            # A flat frame -- a dark detector, or a saturated one. Widen by a count so
            # the colour map has a range to work with instead of dividing by zero.
            hi = lo + 1.0
        self._image.setImage(view, autoLevels=False, levels=(lo, hi))
        # Map the image onto the real axes: x is wavelength, y is the spectrum index.
        self._image.setRect(QRectF(float(wl[0]), 0.0,
                                   float(wl[-1] - wl[0]) or 1.0, float(self._n_rows)))
        self._plot.setTitle(f"{self._n_rows} spectra · {lo:.0f}–{hi:.0f} counts")


    # -- form plumbing ----------------------------------------------------

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
        # Flush the widgets into the bound settings before the recorder reads them, and
        # clear the picture: a new run's first rows next to the previous run's would be
        # read as one continuous record.
        self._apply()
        self._reset_heatmap()
        self._vm.start()

    def _on_load(self) -> None:
        start = self._out_dir_edit.text().strip() or str(Path.cwd())
        chosen, _ = QFileDialog.getOpenFileName(self, "Open a soak recording", start,
                                                "Soak recordings (SOAK_*.h5);;HDF5 (*.h5)")
        if not chosen:
            return
        try:
            run = load_soak(chosen, max_rows=_MAX_ROWS)
        except SoakLoadError as exc:
            # The loader's messages are written for an operator, so they go straight to
            # the status line rather than being wrapped in something vaguer.
            self._status.setText(str(exc))
            return
        self._reset_heatmap()
        self._on_data(run.wavelength_nm, run.counts)
        self._status.setText(run.summary())

    def _on_pause(self) -> None:
        if self._paused:
            self._vm.resume()
        else:
            self._vm.pause()

    def _render(self, text: str, running: bool, paused: bool) -> None:
        self._status.setText(text)
        self._start_btn.setEnabled(not running)
        self._load_btn.setEnabled(not running)
        self._pause_btn.setEnabled(running)
        self._stop_btn.setEnabled(running)
        self._paused = paused and running
        self._pause_btn.setText("Resume" if self._paused else "Pause")

    def on_apply(self) -> None:
        pass
