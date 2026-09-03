"""The Spectrum Soak panel.

A panel, not a routine config form: this is something you *watch*, alongside Phase
Control and the XCORR display, for minutes at a time. The earlier version lived behind
Routines -> Spectrum Soak, which put a live accumulating waterfall inside a modal-ish
settings window -- the wrong shape for a picture whose whole job is to be looked at
while something else is being adjusted.

It records the spectrometer to one HDF5 file and draws it accumulating: wavelength
across, time down, counts as colour. Drift reads as slanted stripes, a held pattern as
straight vertical bars, which is the entire question -- is the loop actually holding? --
answered at a glance, long before the file is analysed.

It starts no stages and does not touch the phase loop. The comparison it exists for is
run by hand: record once with stabilization off, once with it on, from the Phase Control
panel. Deliberately not automated into one button, because "loop on" and "loop off" have
to be the *only* difference between the two files, and a panel that reconfigured the loop
between arms would be changing what it is measuring.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from base_qt.ui.panel import Panel

from app_apps.routines.spectrum_soak.loader import SoakLoadError, load_soak
from app_apps.routines.spectrum_soak.normalize import envelope_normalize
from app_apps.routines.spectrum_soak.ui.view_model import SpectrumSoakViewModel

#: Rows held in the *image*. The file keeps everything; this only bounds what is drawn,
#: so a long soak at period 0 cannot grow the panel's memory without limit. At the far
#: end the oldest rows scroll off the top -- which is the right trade for a display whose
#: whole job is "has it moved recently?", and it is why the file is the record, not this.
_MAX_ROWS = 4000


class SpectrumSoakView(Panel):
    """Panels -> Spectrum Soak. See the module docstring."""

    def __init__(self, vm: SpectrumSoakViewModel, parent: QWidget | None = None) -> None:
        super().__init__("Spectrum Soak", vm, parent)

    # -- construction -----------------------------------------------------

    def setup(self) -> None:
        vm: SpectrumSoakViewModel = self.vm
        s = vm.settings

        # The spectrometer free-runs; the period decimates its stream rather than asking
        # it for a frame. Said here as well as in the docstring because the difference is
        # invisible in the file and shows up as spacing that never quite matches.
        note = QLabel("Period 0 records every spectrum. Otherwise it is a floor on the "
                      "spacing — the spectrometer free-runs, so recorded spacing lands "
                      "between the period and the period plus one frame time. Read the "
                      "time axis from timestamp_ns. The whole detector is always "
                      "recorded; the view options below change only the picture.")
        note.setWordWrap(True)
        self.body_layout.addWidget(note)

        knobs = QHBoxLayout()
        knobs.addWidget(QLabel("Duration"))
        self._duration_spin = self._spin(1.0, 86_400.0, 1, 10.0, " s", s.duration_s)
        knobs.addWidget(self._duration_spin)
        knobs.addSpacing(12)
        knobs.addWidget(QLabel("Period"))
        self._period_spin = self._spin(0.0, 3600.0, 3, 0.5, " s", s.period_s)
        knobs.addWidget(self._period_spin)
        knobs.addStretch(1)
        self.body_layout.addLayout(knobs)

        out_row = QHBoxLayout()
        out_label = QLabel("Output folder")
        out_label.setMinimumWidth(90)
        self._out_dir_edit = QLineEdit(str(s.out_dir))
        self._out_dir_edit.setPlaceholderText("folder for the SOAK_*.h5 files")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse)
        out_row.addWidget(out_label)
        out_row.addWidget(self._out_dir_edit, stretch=1)
        out_row.addWidget(browse_btn)
        self.body_layout.addLayout(out_row)

        tag_row = QHBoxLayout()
        tag_label = QLabel("Tag")
        tag_label.setMinimumWidth(90)
        self._tag_edit = QLineEdit(s.tag)
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
        # one are read the same way. Disabled while recording rather than allowed to
        # overwrite the live picture with a file.
        self._load_btn = QPushButton("Load…")
        self._load_btn.setToolTip("Open a SOAK_*.h5 and draw it here. Recording is "
                                  "unaffected; this only replaces what is on the chart.")
        self._load_btn.clicked.connect(self._on_load)
        for b in (self._start_btn, self._pause_btn, self._stop_btn, self._load_btn):
            controls.addWidget(b)
        controls.addStretch(1)
        self.body_layout.addLayout(controls)

        # View options. None of these touch the recorder or the file -- they are how the
        # picture is drawn, and they stay live while a run is in progress precisely so the
        # operator can go looking without interrupting it.
        options = QHBoxLayout()
        self._norm_cb = QCheckBox("Envelope-normalized")
        self._norm_cb.setToolTip(
            "Map each spectrum's lower envelope to 0 and its upper to 1, so every fringe "
            "is drawn at the same contrast no matter how much light was under it. The "
            "fringes at the edges of the band are invisible in the raw view for want of "
            "counts, not for want of fringes. Display only — the file keeps raw counts.")
        self._norm_cb.toggled.connect(self._redraw)
        options.addWidget(self._norm_cb)
        self._crop_cb = QCheckBox("Crop to ROI")
        self._crop_cb.setToolTip(
            "Draw only the wavelengths between the two green markers, and rescale the "
            "colours to what is left. Drag the markers to adjust. Display only — the "
            "whole detector is still recorded, so this can be changed or undone after "
            "the fact, including on a loaded file.")
        self._crop_cb.toggled.connect(self._on_crop_toggled)
        options.addWidget(self._crop_cb)
        options.addStretch(1)
        self.body_layout.addLayout(options)

        self._status = QLabel("idle")
        self._status.setWordWrap(True)
        self.body_layout.addWidget(self._status)

        self._build_heatmap()

        #: Local mirror of the pause toggle -- the button drives its own label, and the
        #: view model resets it through _render whenever a run ends.
        self._paused = False

        vm.bind_update(self._render)
        vm.bind_data(self._on_data)

    @staticmethod
    def _spin(lo: float, hi: float, decimals: int, step: float,
              suffix: str, value: float) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(lo, hi)
        box.setDecimals(decimals)
        box.setSingleStep(step)
        box.setSuffix(suffix)
        box.setValue(float(value))
        return box

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

        # The panel's OWN region, in nm. Green and solid, matching the ROI markers in the
        # Phase Control panel because they mean the same kind of thing -- but this one is
        # the operator's view onto the data and nothing else reads it. Hidden until the
        # crop is on: a pair of markers parked at the edges reads as a crop that is not
        # there. Committed on release rather than per pixel, since each move rescales the
        # colour map over the whole picture.
        pen = pg.mkPen("#39d353", width=1)
        self._roi_lines: list[pg.InfiniteLine] = []
        for _ in range(2):
            line = pg.InfiniteLine(angle=90, movable=True, pen=pen, label="ROI",
                                   labelOpts={"position": 0.9, "color": "#39d353"})
            line.setZValue(60)
            line.setVisible(False)
            line.sigPositionChangeFinished.connect(self._on_roi_dragged)
            self._plot.addItem(line)
            self._roi_lines.append(line)

    def _reset_heatmap(self) -> None:
        self._buf = None
        self._n_rows = 0
        self._wl = None
        self._image.clear()

    def _on_data(self, wl: np.ndarray, block: np.ndarray) -> None:
        """Accumulate raw rows. Everything about how they LOOK happens in _redraw."""
        if block.ndim != 2 or block.size == 0:
            return
        n_px = block.shape[1]
        if self._buf is None or self._buf.shape[1] != n_px:
            # First block, or the axis changed under us (a re-grating mid-soak). Start
            # over rather than draw two different axes on one picture.
            self._buf = np.empty((max(64, block.shape[0]), n_px), dtype=np.float32)
            self._n_rows = 0
            self._wl = None
        fresh_axis = self._wl is None
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

        if fresh_axis:
            self._park_roi_lines()
        self._redraw()

    # -- the ROI (this panel's own, on the display only) -------------------

    def _park_roi_lines(self) -> None:
        """Put the markers at the edges of the data. Called when the axis first arrives,
        so they always open somewhere the operator can see and grab."""
        wl = self._wl
        if wl is None or wl.size < 2 or not self._roi_lines:
            return
        span = float(wl[-1] - wl[0])
        for line, frac in zip(self._roi_lines, (0.25, 0.75)):
            line.setPos(float(wl[0]) + span * frac)

    def _roi_slice(self) -> slice:
        """Columns to draw. The whole span unless the crop is on and the markers select
        at least a few pixels -- a crop down to nothing is a slip of the mouse, not an
        instruction, and blanking the panel is a poor way to report it."""
        wl = self._wl
        if wl is None or not self._crop_cb.isChecked() or not self._roi_lines:
            return slice(None)
        lo, hi = sorted(float(ln.value()) for ln in self._roi_lines)
        idx = np.nonzero((wl >= lo) & (wl <= hi))[0]
        if idx.size < 4:
            return slice(None)
        return slice(int(idx[0]), int(idx[-1]) + 1)

    def _on_crop_toggled(self, on: bool) -> None:
        for line in self._roi_lines:
            line.setVisible(on)
        self._redraw()

    def _on_roi_dragged(self, _line) -> None:
        self._redraw()

    # -- drawing ----------------------------------------------------------

    def _redraw(self, *_ignored) -> None:
        """Buffer -> picture. Crop, then normalise, then scale the colours to what is
        left, in that order: normalising before the crop would set every fringe's
        contrast from envelopes measured partly outside the region being looked at."""
        if self._buf is None or self._n_rows == 0 or self._wl is None:
            return
        cut = self._roi_slice()
        wl = self._wl[cut]
        view = self._buf[:self._n_rows, cut]
        if wl.size < 2 or view.size == 0:
            return

        normalized = self._norm_cb.isChecked()
        if normalized:
            view = envelope_normalize(view)

        finite = view[np.isfinite(view)]
        if finite.size == 0:
            return
        lo, hi = float(finite.min()), float(finite.max())
        if hi <= lo:
            # A flat frame -- a dark detector, or a saturated one. Widen so the colour
            # map has a range to work with instead of dividing by zero.
            hi = lo + 1.0
        self._image.setImage(view, autoLevels=False, levels=(lo, hi))
        # Map the image onto the real axes: x is wavelength, y is the spectrum index.
        self._image.setRect(QRectF(float(wl[0]), 0.0,
                                   float(wl[-1] - wl[0]) or 1.0, float(self._n_rows)))
        units = "fringe" if normalized else "counts"
        span = f" · {wl[0]:.2f}–{wl[-1]:.2f} nm" if cut != slice(None) else ""
        self._plot.setTitle(f"{self._n_rows} spectra · {lo:.2f}–{hi:.2f} {units}{span}")

    # -- commands ---------------------------------------------------------

    def _flush_settings(self) -> None:
        """Widgets -> settings, done once at Start rather than on every keystroke.

        A blank folder box means "leave it alone" rather than "write to the root", so the
        value actually in force is echoed back and the two cannot disagree.
        """
        s = self.vm.settings
        s.duration_s = float(self._duration_spin.value())
        s.period_s = float(self._period_spin.value())
        text = self._out_dir_edit.text().strip()
        if text:
            s.out_dir = Path(text)
        self._out_dir_edit.setText(str(s.out_dir))
        s.tag = self._tag_edit.text().strip()

    def _on_browse(self) -> None:
        start = self._out_dir_edit.text().strip() or str(Path.cwd())
        chosen = QFileDialog.getExistingDirectory(self, "Soak output folder", start)
        if chosen:
            self._out_dir_edit.setText(str(Path(chosen)))

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

    def _on_start(self) -> None:
        # Clear the picture first: a new run's rows drawn under the previous run's would
        # be read as one continuous record.
        self._flush_settings()
        self._reset_heatmap()
        self.vm.start()

    def _on_pause(self) -> None:
        if self._paused:
            self.vm.resume()
        else:
            self.vm.pause()

    def _render(self, text: str, running: bool, paused: bool) -> None:
        self._status.setText(text)
        self._start_btn.setEnabled(not running)
        self._load_btn.setEnabled(not running)
        self._pause_btn.setEnabled(running)
        self._stop_btn.setEnabled(running)
        self._duration_spin.setEnabled(not running)
        self._period_spin.setEnabled(not running)
        self._paused = paused and running
        self._pause_btn.setText("Resume" if self._paused else "Pause")
