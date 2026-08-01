"""The XCORR display panel (spec R12–R15).

A read-only companion to the XCORR Scan routine, modelled on the Phase Control panel.
Four linked plots over the current run's scan history:

  ┌ intensity : ⟨V₊⟩ vs probe ─────────────┐   R12  (live, in-flight scan is last)
  ├ frequency : |f|  vs probe ─────────────┤   R13  (per finished scan, x-linked above)
  ├ summary A : f₀ vs Δt, one series per L ─┤   R14
  └ summary B : Δf vs L,  one series per Δt ┘   R14

The two per-scan panels share one selection (scroll bar + ◀/▶) and one x-axis, and
both carry a **stage ↔ time** toggle (t = 2x/c). Everything is recomputed from the
:class:`XcorrDisplayViewModel`'s :class:`Scan` records.

Those records come either from the live run's bus events or, via **Import run…**, from
a finished run's ``.h5``. The view does not care which: an imported run populates the
same records and redraws through the same slots, so every plot, the fit overlay and
both summary panels work identically on archived data.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from base_qt.ui.panel import Panel

from app_apps.analysis.xcorr.frequency import C_MM_PER_PS, probe_mm_to_ps
from app_apps.analysis.xcorr.ui.xcorr_display_view_model import Scan, XcorrDisplayViewModel

#: Distinct pen colours for the summary series (one per L, and one per Δt).
_SERIES_COLOURS = [
    "#22867f", "#d19226", "#0b4a46", "#9a6011",
    "#6fc9c0", "#f2cf76", "#563304", "#54c7bd",
]


def _series_pen(i: int) -> pg.mkPen:
    return pg.mkPen(_SERIES_COLOURS[i % len(_SERIES_COLOURS)], width=2)


class XcorrDisplayView(Panel):
    def __init__(self, vm: XcorrDisplayViewModel, parent: QWidget | None = None) -> None:
        super().__init__("XCORR Display", vm, parent)

    # -- construction -----------------------------------------------------

    def setup(self) -> None:
        pg.setConfigOptions(antialias=True)
        self.body_layout.addWidget(self._build_status_header())
        self.body_layout.addWidget(self._build_controls())

        # --- per-scan panels: intensity over frequency, x-axes linked (R12) ---
        self._intensity = pg.PlotWidget()
        self._intensity.showGrid(x=True, y=True, alpha=0.3)
        self._intensity.setLabel("left", "⟨V₊⟩ (a.u.)")
        self._intensity.setMinimumHeight(150)
        self._live_curve = self._intensity.plot(pen=pg.mkPen("#0f6b66", width=2))
        # Reconstructed fringe (fit model) overlaid on the raw curve, toggled off by
        # default. Dashed amber so it reads clearly on top of the solid teal raw trace.
        self._recon_curve = self._intensity.plot(
            pen=pg.mkPen("#d19226", width=1.5, style=Qt.DashLine))
        # The fitted envelopes. Always drawn (unlike the reconstruction, which is
        # toggled): they are the envelope half of the fit, which is the robust half,
        # and their separation is what makes the reconstruction's amplitude-free
        # behaviour legible rather than confusing — see FringeFit.contrast.
        env_pen = pg.mkPen("#0b4a46", width=1, style=Qt.DotLine)
        self._env_upper = self._intensity.plot(pen=env_pen)
        self._env_lower = self._intensity.plot(pen=env_pen)

        self._frequency = pg.PlotWidget()
        self._frequency.showGrid(x=True, y=True, alpha=0.3)
        self._frequency.setLabel("left", "|f| (GHz)")
        self._frequency.setMinimumHeight(150)
        self._frequency.setXLink(self._intensity)  # synced scroll/zoom (R12)
        # Mouse/scroll zooms the horizontal axis only; the vertical axis is driven
        # programmatically to a range shared across all scans (see _apply_y_bounds),
        # so navigating scans keeps a comparable amplitude/frequency scale.
        for pw in (self._intensity, self._frequency):
            vb = pw.getViewBox()
            vb.setMouseEnabled(x=True, y=False)
            vb.enableAutoRange(axis=vb.YAxis, enable=False)
        # ±1σ band on f(t). The two edge curves are invisible scaffolding for the
        # fill; a band rather than per-point bars because every point shares the same
        # four fitted coefficients, so the errors are near-perfectly correlated and
        # independent bars would suggest scatter that is not there.
        self._freq_band_hi = self._frequency.plot(pen=None)
        self._freq_band_lo = self._frequency.plot(pen=None)
        self._freq_band = pg.FillBetweenItem(
            self._freq_band_hi, self._freq_band_lo, brush=pg.mkBrush(154, 96, 17, 60))
        self._freq_band.setVisible(False)
        self._frequency.addItem(self._freq_band)
        self._freq_curve = self._frequency.plot(pen=pg.mkPen("#9a6011", width=2))
        self._f0_marker = self._frequency.plot(
            pen=None, symbol="o", symbolBrush="#0f6b66", symbolSize=9)
        self._nyquist_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#b04a3a", style=Qt.DashLine))
        self._nyquist_line.setVisible(False)
        self._frequency.addItem(self._nyquist_line)

        self._readout = QLabel("no scans yet")
        self._readout.setTextFormat(Qt.PlainText)

        self.body_layout.addWidget(self._intensity, stretch=3)
        self.body_layout.addWidget(self._frequency, stretch=3)
        self.body_layout.addWidget(self._readout)

        # --- grid summary panels (R14), side by side — the panel now gets the full
        # window width (it is a tab, not squeezed next to Phase Control), so there is
        # room for both. ---
        summary = QWidget()
        srow = QHBoxLayout(summary)
        srow.setContentsMargins(0, 0, 0, 0)
        self._sum_f0 = pg.PlotWidget()
        self._sum_f0.showGrid(x=True, y=True, alpha=0.3)
        self._sum_f0.setLabel("left", "f₀ (GHz)")
        self._sum_f0.setLabel("bottom", "Δt (ps)")
        self._sum_f0.setMinimumHeight(150)
        self._sum_f0_legend = self._sum_f0.addLegend(offset=(-10, 10))

        self._sum_bw = pg.PlotWidget()
        self._sum_bw.showGrid(x=True, y=True, alpha=0.3)
        self._sum_bw.setLabel("left", "Δf (GHz)")
        self._sum_bw.setLabel("bottom", "L (mm)")
        self._sum_bw.setMinimumHeight(150)
        self._sum_bw_legend = self._sum_bw.addLegend(offset=(-10, 10))

        srow.addWidget(self._sum_f0)
        srow.addWidget(self._sum_bw)
        self.body_layout.addWidget(summary, stretch=2)

        # X-axis fit/zoom state (see _apply_x_bounds): the full probe span currently
        # in view, and what was drawn last, so a live update can tell "grow the view"
        # from "the user has zoomed in, leave them be".
        self._x_full_span: float | None = None
        self._last_idx: int = -1
        self._last_time_axis: bool = False
        #: Directory the last import came from, so a second import starts where the
        #: first one did rather than back at the default.
        self._last_import_dir: str = ""

        self._apply_axis_labels()
        self._connect(self.vm.history_changed, self._refresh_nav)
        self._connect(self.vm.selection_changed, self._redraw_selection)
        self._connect(self.vm.summary_changed, self._redraw_summary)
        self._connect(self.vm.status_changed, self._render_status)
        self._connect(self.vm.run_label_changed, self._render_run_label)
        self._connect(self.vm.fit_progress_changed, self._render_fit_progress)
        self._render_status()

    def _build_status_header(self) -> QWidget:
        box = QGroupBox("Scan position")
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(box)
        v.setSpacing(3)

        # Which run is on display. Empty (and hidden) for a live run, which already
        # announces itself through the progress bar; shown only for an import, where
        # otherwise nothing on screen says the data is not from the instrument.
        self._run_label = QLabel("")
        self._run_label.setWordWrap(True)
        self._run_label.setVisible(False)
        v.addWidget(self._run_label)

        # How many queued fits are still outstanding. Sits with the run label because
        # that is where the eye already is after an import — the one moment when the
        # panel has tens of seconds of analysis to get through and would otherwise
        # look frozen. Hidden whenever nothing is queued.
        self._fit_label = QLabel("")
        self._fit_label.setVisible(False)
        v.addWidget(self._fit_label)

        # Overall progress across all probe points of the run.
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setFormat("idle")
        v.addWidget(self._progress)

        # One position bar per axis: fill = how far the current position sits within
        # that axis's scan range (grating creeps monotonically; delay and probe cycle).
        self._axis_bars: dict[str, QProgressBar] = {}
        for label, _cur, _rng, _unit in self.vm.axis_status():
            row = QHBoxLayout()
            lab = QLabel(label)
            lab.setMinimumWidth(56)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(0)
            bar.setFormat("—")
            bar.setFixedHeight(16)
            row.addWidget(lab)
            row.addWidget(bar, stretch=1)
            v.addLayout(row)
            self._axis_bars[label] = bar
        return box

    def _render_status(self, *_: object) -> None:
        n, done = self.vm.n_points, self.vm.points_done
        if n > 0:
            self._progress.setRange(0, n)
            self._progress.setValue(min(done, n))
            self._progress.setFormat(f"point {done} / {n}  (%p%)")
        else:
            self._progress.setRange(0, 1)
            self._progress.setValue(0)
            self._progress.setFormat("idle — no run")

        for label, cur, rng, unit in self.vm.axis_status():
            bar = self._axis_bars[label]
            if cur is None or rng is None:
                bar.setValue(0)
                bar.setFormat("—")
                continue
            lo, hi = rng
            if hi > lo:
                frac = min(1.0, max(0.0, (cur - lo) / (hi - lo)))
                bar.setValue(int(round(1000 * frac)))
            else:
                bar.setValue(1000)  # single-value axis: always "at" its one position
            bar.setFormat(f"{cur:.2f} {unit}   [{lo:.2f} … {hi:.2f}]")

    def _build_controls(self) -> QWidget:
        # Two rows: navigation on top, view/analysis controls beneath. A single row
        # overflows the dock width and clips the right-hand widgets (the axis toggle
        # among them), so the toggle must not share a row with the stretchy scroll bar.
        bar = QWidget()
        bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.setSpacing(4)

        # --- row 1: scan navigation ---
        nav_row = QHBoxLayout()
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(34)
        self._prev_btn.clicked.connect(lambda: self.vm.step(-1))
        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(34)
        self._next_btn.clicked.connect(lambda: self.vm.step(+1))

        self._nav = QScrollBar(Qt.Horizontal)
        self._nav.setMinimum(0)
        self._nav.setMaximum(0)
        self._nav.valueChanged.connect(self._on_nav)

        self._scan_label = QLabel("—")
        self._scan_label.setMinimumWidth(220)

        nav_row.addWidget(self._prev_btn)
        nav_row.addWidget(self._next_btn)
        nav_row.addWidget(self._nav, stretch=1)
        nav_row.addWidget(self._scan_label)

        # --- row 2: view + analysis controls ---
        opt_row = QHBoxLayout()
        self._follow = QCheckBox("Follow live")
        self._follow.setChecked(True)
        self._follow.toggled.connect(self.vm.set_follow_live)

        # Stage ↔ time toggle (R12). Unchecked = stage (mm); checked = time (ps).
        self._time_toggle = QPushButton("Stage (mm)")
        self._time_toggle.setCheckable(True)
        self._time_toggle.toggled.connect(self._on_time_toggle)

        # Overlay the reconstructed fringe (fit model) on the raw curve.
        self._overlay_toggle = QPushButton("Overlay fit")
        self._overlay_toggle.setCheckable(True)
        self._overlay_toggle.setToolTip(
            "Overlay the reconstructed fringe (fitted envelope × phase model) on the raw curve")
        self._overlay_toggle.toggled.connect(lambda _=False: self._redraw_selection())

        self._window = QDoubleSpinBox()
        self._window.setRange(1.0, 100_000.0)
        self._window.setDecimals(1)
        self._window.setSuffix(" ps")
        self._window.setValue(self.vm.window_ps)
        self._window.setToolTip("Bandwidth readout window W (C23) — re-fits on change")
        self._window.editingFinished.connect(lambda: self.vm.set_window_ps(self._window.value()))

        self._gzero = QDoubleSpinBox()
        self._gzero.setRange(-1000.0, 1000.0)
        self._gzero.setDecimals(3)
        self._gzero.setSuffix(" mm")
        self._gzero.setValue(self.vm.grating_zero_mm)
        self._gzero.setToolTip("Grating zero-separation L=0 (C22) — summary x-axis only")
        self._gzero.editingFinished.connect(lambda: self.vm.set_grating_zero_mm(self._gzero.value()))

        # Import a finished run from disk (the loader's door 2). Sits with the view
        # controls rather than navigation: it changes *what* is on display, like the
        # axis toggle, not where you are within it.
        self._import_btn = QPushButton("Import run…")
        self._import_btn.setToolTip(
            "Load a finished XCORR .h5 and analyse it exactly like a live run")
        self._import_btn.clicked.connect(self._on_import)

        opt_row.addWidget(self._import_btn)
        opt_row.addSpacing(10)
        opt_row.addWidget(self._follow)
        opt_row.addSpacing(10)
        opt_row.addWidget(QLabel("Axis:"))
        opt_row.addWidget(self._time_toggle)
        opt_row.addSpacing(10)
        opt_row.addWidget(self._overlay_toggle)
        opt_row.addStretch(1)
        opt_row.addWidget(QLabel("W"))
        opt_row.addWidget(self._window)
        opt_row.addSpacing(10)
        opt_row.addWidget(QLabel("L₀"))
        opt_row.addWidget(self._gzero)

        outer.addLayout(nav_row)
        outer.addLayout(opt_row)
        return bar

    # -- control slots ----------------------------------------------------

    def _on_nav(self, value: int) -> None:
        # Guard against the feedback loop: _refresh_nav sets the bar programmatically.
        if self._nav.signalsBlocked():
            return
        self.vm.select(value)

    def _on_import(self) -> None:
        """Pick a run file and hand it to the view-model.

        Opens in the last-used directory, falling back to the imported run's folder and
        then the working directory — the runs live under ``xcorr_runs/`` beside the app,
        so the default lands the operator close to them without hard-coding a path.
        """
        start = self._last_import_dir or str(Path.cwd() / "xcorr_runs")
        if not Path(start).is_dir():
            start = str(Path.cwd())
        path, _ = QFileDialog.getOpenFileName(
            self, "Import XCORR run", start, "XCORR run (*.h5);;All files (*)")
        if not path:
            return
        self._last_import_dir = str(Path(path).parent)
        error = self.vm.import_run(path)
        if error:
            # The panel stays on whatever it was showing; the header carries the reason.
            self._run_label.setText(f"Import failed — {error}")
            self._run_label.setVisible(True)

    def _render_fit_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self._fit_label.setVisible(False)
            self._fit_label.setText("")
            return
        self._fit_label.setText(f"fitting {done} / {total} scans…")
        self._fit_label.setVisible(True)

    def _render_run_label(self, text: str) -> None:
        self._run_label.setText(f"Imported: {text}" if text else "")
        self._run_label.setVisible(bool(text))

    def _on_time_toggle(self, checked: bool) -> None:
        self._time_toggle.setText("Time (ps)" if checked else "Stage (mm)")
        self._apply_axis_labels()
        self._redraw_selection()

    @property
    def _time_axis(self) -> bool:
        return self._time_toggle.isChecked()

    # -- rendering --------------------------------------------------------

    def _refresh_nav(self, *_: object) -> None:
        n = self.vm.scan_count
        self._nav.blockSignals(True)
        self._nav.setMaximum(max(0, n - 1))
        if self.vm.selected_index >= 0:
            self._nav.setValue(self.vm.selected_index)
        self._nav.blockSignals(False)
        self._prev_btn.setEnabled(n > 0)
        self._next_btn.setEnabled(n > 0)
        self._follow.blockSignals(True)
        self._follow.setChecked(self.vm.follow_live)
        self._follow.blockSignals(False)
        self._redraw_selection()

    def _apply_axis_labels(self) -> None:
        label = "Delay t (ps)" if self._time_axis else "Probe stage (mm)"
        self._intensity.setLabel("bottom", label)
        self._frequency.setLabel("bottom", label)

    def _redraw_selection(self, *_: object) -> None:
        scan = self.vm.selected_scan()
        if scan is None:
            self._live_curve.setData([], [])
            self._recon_curve.setData([], [])
            self._env_upper.setData([], [])
            self._env_lower.setData([], [])
            self._freq_curve.setData([], [])
            self._freq_band.setVisible(False)
            self._f0_marker.setData([], [])
            self._nyquist_line.setVisible(False)
            self._scan_label.setText("—")
            self._readout.setText("no scans yet")
            self._x_full_span = None
            return

        n = self.vm.scan_count
        self._scan_label.setText(f"scan {self.vm.selected_index + 1}/{n}   {scan.label}")

        xi, yi = self._intensity_xy(scan)
        self._live_curve.setData(xi, yi)

        self._draw_frequency(scan)
        self._draw_overlay(scan)
        self._apply_x_bounds(xi)
        self._apply_y_bounds()

    def _fit_x(self, trace) -> np.ndarray:
        """The fit-core x-axis in the current mode — shared by the frequency curve and
        the raw-overlay so they register exactly with the intensity trace."""
        if self._time_axis:
            return trace.t_centred_ps                 # μ at 0
        return trace.t_ps * C_MM_PER_PS / 2.0          # ps → stage mm (t = 2x/c)

    def _draw_overlay(self, scan: Scan) -> None:
        """Reconstruction (toggled) and fitted envelopes (always) on the raw panel.

        Both hang off the same fit, so an in-flight scan with no fit yet simply
        clears them rather than being a special case anywhere else.
        """
        trace = scan.trace
        fitted = trace is not None and trace.ok and trace.recon_signal.size > 0
        if not fitted:
            self._recon_curve.setData([], [])
            self._env_upper.setData([], [])
            self._env_lower.setData([], [])
            return

        x = self._fit_x(trace)
        if self._overlay_toggle.isChecked():
            self._recon_curve.setData(x, trace.recon_signal)
        else:
            self._recon_curve.setData([], [])
        self._env_upper.setData(x, trace.env_upper)
        self._env_lower.setData(x, trace.env_lower)

    def _apply_x_bounds(self, x: np.ndarray) -> None:
        """Fit the shared x-axis to the probe range and cap zoom-out at that span.

        ``setLimits`` bounds panning to the data and forbids zooming *out* past the
        full span; nothing bounds zoom-*in*. The view is re-fit to the full span when
        the scan/axis changed, or when it was already showing the full span (so a live
        scan's view grows with it) — but a deliberate zoom-in is left untouched.
        """
        if x.size < 2:
            return
        xmin, xmax = float(np.min(x)), float(np.max(x))
        span = xmax - xmin
        if span <= 0:
            return

        vb = self._intensity.getViewBox()
        for pw in (self._intensity, self._frequency):
            pw.getViewBox().setLimits(xMin=xmin, xMax=xmax, maxXRange=span)

        idx = self.vm.selected_index
        changed = idx != self._last_idx or self._time_axis != self._last_time_axis
        cur_lo, cur_hi = vb.viewRange()[0]
        was_full = self._x_full_span is None or (cur_hi - cur_lo) >= self._x_full_span - 1e-6
        if changed or was_full:
            vb.setXRange(xmin, xmax, padding=0)  # linked, so the frequency plot follows

        self._x_full_span = span
        self._last_idx = idx
        self._last_time_axis = self._time_axis

    def _apply_y_bounds(self) -> None:
        """Set each scan panel's y-range to span *all* scans, not just the selected one.

        The vertical axis is shared across the run so amplitudes and frequencies are
        directly comparable as you step through scans; it is not user-zoomable (the
        mouse is x-only), so it is safe to drive it here on every redraw.
        """
        # The fitted overlays have to be inside the range too, or the additions they
        # exist to make visible get clipped: the envelopes bracket the data by
        # construction, and f ± σ brackets f.
        fitted = self.vm.finished_scans()

        raw = [np.asarray(s.v_mean_pos, float) for s in self.vm.scans if s.v_mean_pos]
        env = [a for s in fitted for a in (s.trace.env_lower, s.trace.env_upper) if a.size]
        if raw:
            self._set_shared_y(self._intensity, *_span(raw + env, _span(raw)))

        f_arrays = [s.trace.f_ghz for s in fitted if s.trace.f_ghz.size]
        band = [s.trace.f_ghz + np.nan_to_num(s.trace.f_sigma_ghz)
                for s in fitted if s.trace.f_sigma_ghz.size == s.trace.f_ghz.size]
        if f_arrays:
            self._set_shared_y(self._frequency, *_span(f_arrays + band, _span(f_arrays)))

    @staticmethod
    def _set_shared_y(pw: pg.PlotWidget, lo: float, hi: float) -> None:
        if not (np.isfinite(lo) and np.isfinite(hi)):
            return
        if hi <= lo:
            hi = lo + 1.0
        pad = 0.05 * (hi - lo)
        pw.getViewBox().setYRange(lo - pad, hi + pad, padding=0)

    def _intensity_xy(self, scan: Scan) -> tuple[np.ndarray, np.ndarray]:
        probe = np.asarray(scan.probe_mm, float)
        y = np.asarray(scan.v_mean_pos, float)
        if probe.size == 0:
            return probe, y
        order = np.argsort(probe)
        probe, y = probe[order], y[order]
        if not self._time_axis:
            return probe, y
        t = probe_mm_to_ps(probe)
        # Finished scans are centred on the fitted envelope centre μ so the grid is
        # comparable (R12); an in-flight scan has no μ yet, so it stays uncentred.
        if scan.finished and scan.trace is not None and scan.trace.ok:
            t = t - scan.trace.t_mu_ps
        return t, y

    def _draw_frequency(self, scan: Scan) -> None:
        trace = scan.trace
        if trace is None or not trace.ok or trace.t_ps.size == 0:
            self._freq_curve.setData([], [])
            self._freq_band.setVisible(False)
            self._f0_marker.setData([], [])
            self._nyquist_line.setVisible(False)
            self._readout.setText(self._readout_text(scan))
            return

        x = self._fit_x(trace)
        x_mu = 0.0 if self._time_axis else trace.t_mu_ps * C_MM_PER_PS / 2.0
        self._freq_curve.setData(x, trace.f_ghz)
        self._draw_frequency_band(x, trace)
        self._f0_marker.setData([x_mu], [trace.f_central_ghz])

        if np.isfinite(trace.nyquist_ghz):
            self._nyquist_line.setValue(trace.nyquist_ghz)
            self._nyquist_line.setVisible(True)
        else:
            self._nyquist_line.setVisible(False)

        self._readout.setText(self._readout_text(scan))

    def _draw_frequency_band(self, x: np.ndarray, trace) -> None:
        """Fill ``|f| ± σ(t)``, skipping the band entirely if σ is not usable.

        ``f_ghz`` is ``|f|``, so the band is built around the absolute value: near a
        zero crossing that folds, which is honest — the sign is not measured.
        """
        sigma = trace.f_sigma_ghz
        if sigma.size != x.size or not np.any(np.isfinite(sigma)):
            self._freq_band.setVisible(False)
            return
        s = np.where(np.isfinite(sigma), sigma, 0.0)
        self._freq_band_hi.setData(x, trace.f_ghz + s)
        self._freq_band_lo.setData(x, np.maximum(trace.f_ghz - s, 0.0))
        # No setCurves() here: the item is already bound to these two and repaints
        # itself off their sigPlotChanged.
        self._freq_band.setVisible(True)

    @staticmethod
    def _readout_text(scan: Scan) -> str:
        trace = scan.trace
        if not scan.finished:
            return "in flight — frequency fit runs when the scan completes"
        if trace is None:
            return "fitting…"
        if not trace.ok:
            return f"fit failed: {trace.status}"
        flag = "trusted" if trace.trusted else f"⚠ {trace.status}"
        return (
            f"f₀ = {trace.f_central_ghz:.2f} ± {trace.f_central_sigma_ghz:.2f} GHz     "
            f"Δf = {trace.bandwidth_ghz:.2f} ± {trace.bandwidth_sigma_ghz:.2f} GHz     "
            f"r² = {trace.r2_fringe:.3f}     Nyquist = {trace.nyquist_ghz:.0f} GHz     "
            f"[{flag}]"
        )

    def _redraw_summary(self, *_: object) -> None:
        scans = self.vm.finished_scans()

        # f₀ vs Δt, one series per grating separation L (R14).
        self._sum_f0.clear()
        self._sum_f0_legend.clear()
        by_l: dict[float, list[Scan]] = {}
        for s in scans:
            by_l.setdefault(round(self.vm.separation_mm(s), 6), []).append(s)
        for i, l in enumerate(sorted(by_l)):
            pts = sorted(by_l[l], key=self.vm.delta_t_ps)
            x = np.array([self.vm.delta_t_ps(s) for s in pts])
            y = np.array([s.trace.f_central_ghz for s in pts])
            err = np.array([_finite(s.trace.f_central_sigma_ghz) for s in pts])
            pen = _series_pen(i)
            self._sum_f0.plot(x, y, pen=pen, symbol="o", symbolBrush=pen.color(),
                              symbolSize=7, name=f"L = {l:g} mm")
            self._sum_f0.addItem(pg.ErrorBarItem(x=x, y=y, height=2 * err, pen=pen))

        # Δf vs L, one series per base delay Δt (R14).
        self._sum_bw.clear()
        self._sum_bw_legend.clear()
        by_dt: dict[float, list[Scan]] = {}
        for s in scans:
            by_dt.setdefault(round(self.vm.delta_t_ps(s), 6), []).append(s)
        for i, dt in enumerate(sorted(by_dt)):
            pts = sorted(by_dt[dt], key=self.vm.separation_mm)
            x = np.array([self.vm.separation_mm(s) for s in pts])
            y = np.array([s.trace.bandwidth_ghz for s in pts])
            err = np.array([_finite(s.trace.bandwidth_sigma_ghz) for s in pts])
            pen = _series_pen(i)
            self._sum_bw.plot(x, y, pen=pen, symbol="o", symbolBrush=pen.color(),
                              symbolSize=7, name=f"Δt = {dt:g} ps")
            self._sum_bw.addItem(pg.ErrorBarItem(x=x, y=y, height=2 * err, pen=pen))


def _span(arrays: list[np.ndarray],
          fallback: tuple[float, float] | None = None) -> tuple[float, float]:
    """(min, max) over several arrays, ignoring NaNs.

    ``fallback`` is what to return if nothing finite survives — the overlays are
    allowed to contribute NaN, and a y-range must never be lost because one fitted
    envelope came back degenerate.
    """
    finite = [a[np.isfinite(a)] for a in arrays if a.size]
    finite = [a for a in finite if a.size]
    if not finite:
        return fallback if fallback is not None else (0.0, 1.0)
    allv = np.concatenate(finite)
    return float(allv.min()), float(allv.max())


def _finite(v: float) -> float:
    """Error bars want a real half-height — a NaN σ draws nothing, which is fine."""
    return float(v) if np.isfinite(v) else 0.0
