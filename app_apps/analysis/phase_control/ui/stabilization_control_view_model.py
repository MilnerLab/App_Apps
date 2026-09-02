from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPen

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_core.math.enums import AngleUnit
from base_core.math.models import Angle
from base_core.quantities.constants import SPEED_OF_LIGHT
from base_core.quantities.enums import Prefix
from base_qt.app.dispatcher import QtDispatcher
from app_apps.analysis.phase_control.events import (
    PhaseCorrectionReported,
    PhaseMeanReported,
    PhaseTemplateChanged,
    PhaseTrackingStateChanged,
    StabilizationConfigChanged,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_template import PhaseTemplate
from app_apps.io.control_readout.rgv.events import NewRGVAngle, RequestCurrentRGVAngle
from app_apps.analysis.phase_control.subprocess.domain import fringe_core as fc
from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import display_curve

# The OFF state, which now means one thing only: fast correction is selected. Slow mode
# arms its capture the moment stabilization starts, so OFF is no longer something the
# operator can arrive at by not having pressed a button. The wording says what the loop is
# doing rather than presenting the absence of a template as a fault.
_TEMPLATE_OFF_TEXT = "Fast correction — per-frame fit, no reference"

if TYPE_CHECKING:
    from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
    from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig


class StabilizationControlViewModel(QObject):
    worker_state_changed = Signal(object)  # WorkerStatus
    config_updated = Signal()              # subprocess synced new fit params
    plot_mode_changed = Signal(bool)       # plot-in-frequency toggled
    template_state_changed = Signal(str)   # human-readable frozen-template state
    knife_edges_changed = Signal(bool)     # knife-edge markers toggled
    recall_changed = Signal(bool)          # a recalled template is pinned / was deselected
    traces_changed = Signal()              # a curve was toggled on or off
    raw_visible_changed = Signal(bool)     # live spectrum curve, owned by the chart view
    plate_status_changed = Signal(str)     # waveplate angle / last command / next correction
    cut_left_changed = Signal(float, bool)  # terminal nm, True when manually set

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: PhaseStabilizationHandle,
        config: StabilizationConfig,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._dispatcher = dispatcher
        self._handle = handle
        self._config = config
        self._plot_item: pg.PlotItem | None = None
        self._set_phase_series: pg.PlotDataItem | None = None
        self._current_phase_series: pg.PlotDataItem | None = None
        self._rf_label: pg.TextItem | None = None
        self._fwhm_lines: list[pg.InfiniteLine] = []
        self._knife_lines: list[pg.InfiniteLine] = []
        self._template_series: pg.PlotDataItem | None = None
        self._mean_series: pg.PlotDataItem | None = None
        # Which curves are drawn. All three on by default: the point of the chart is the
        # comparison between them, and one missing reads as a loop that is not running.
        self._show_raw = True
        self._show_mean = True
        self._show_target = True
        # The circular running mean the NEXT correction will be made from, as an absolute
        # phase at the reference wavelength. This is the number the loop acts on -- the raw
        # trace is one frame of the noise it is averaging away.
        self._mean_phase: float | None = None
        # The shape the panel is showing, and whether it is a pinned recall. Held on this
        # side so Recall draws the curve IMMEDIATELY, off the file, without waiting for the
        # subprocess -- Recall is normally pressed while stabilization is stopped, and there
        # is then no tracker to echo it back.
        self._template: PhaseTemplate | None = None
        self._pinned = False
        self._active = False
        self._plot_frequency = False
        self._show_knife_edges = False
        self._unsub = bus.subscribe(PhaseTrackingStateChanged, self._on_state_changed)
        self._unsub_cfg = bus.subscribe(StabilizationConfigChanged, self._on_config_updated)
        self._unsub_tpl = bus.subscribe(PhaseTemplateChanged, self._on_template_changed)
        self._template_text = _TEMPLATE_OFF_TEXT

        # --- waveplate readout -------------------------------------------------------
        # Three numbers the operator otherwise has to infer: where the plate is, what the
        # loop last told it to do, and how long until it acts again. The countdown is driven
        # by a local 1 Hz timer against the last correction instant rather than by traffic
        # from the subprocess -- the loop only speaks once per period, and a readout that
        # updates once every 15 s is not a countdown.
        self._unsub_corr = bus.subscribe(PhaseCorrectionReported, self._on_correction)
        self._unsub_mean = bus.subscribe(PhaseMeanReported, self._on_mean)
        self._unsub_rgv = bus.subscribe(NewRGVAngle, self._on_plate_angle)
        self._plate_deg: float | None = None
        self._last_command: str = "—"
        self._period_s = float(config.correction_period_s)
        self._next_at: float | None = None
        self._plate_text = "Waveplate: —"
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._refresh_plate_text)
        self._tick.start()
        # The plate only announces itself when it moves, so ask once rather than showing a
        # dash until the loop's first correction lands.
        bus.publish(RequestCurrentRGVAngle())

    def set_chart(self, plot_item: pg.PlotItem) -> None:
        self._plot_item = plot_item

        # Cosmetic so dash lengths are in screen pixels, not data units — without it,
        # the wavelength (nm) vs intensity (0-1) axes' mismatched scale stretches each
        # dash into a long diagonal streak, making the curve look like jagged noise.
        set_phase_pen = QPen(QColor("red"))
        set_phase_pen.setStyle(Qt.PenStyle.DashLine)
        set_phase_pen.setCosmetic(True)
        self._set_phase_series = pg.PlotDataItem(pen=set_phase_pen)

        # The frozen template: what the loop is holding the fringes AGAINST. Solid yellow,
        # deliberately unlike the two dashed fit overlays -- it is a measurement that was
        # made once, not a curve recomputed from the live frame.
        template_pen = QPen(QColor("yellow"))
        template_pen.setCosmetic(True)
        self._template_series = pg.PlotDataItem(pen=template_pen)

        # The averaged phase the loop is ACTUALLY correcting on: the reference shape moved
        # to where the circular mean of the last ~1/gain frames says the fringes are. Solid
        # blue and thicker than the overlays -- it is the only curve on the chart that is
        # neither a single frame nor a frozen record, and the correction is computed from
        # the gap between it and the target.
        mean_pen = QPen(QColor("#3fa9f5"))
        mean_pen.setWidth(2)
        mean_pen.setCosmetic(True)
        self._mean_series = pg.PlotDataItem(pen=mean_pen)

        current_phase_pen = QPen(QColor("green"))
        current_phase_pen.setStyle(Qt.PenStyle.DashDotLine)
        current_phase_pen.setCosmetic(True)
        self._current_phase_series = pg.PlotDataItem(pen=current_phase_pen)

        # FWHM markers -- the two wavelengths the f_cfg readout quotes its values AT.
        # Deliberately unlike the two fit overlays (red dashed / green dash-dot): these are
        # cyan DOTTED verticals, so the eye separates "where the band ends" from "what the
        # fit says the fringes do". Cosmetic for the same reason the overlay pens are.
        fwhm_pen = QPen(QColor("cyan"))
        fwhm_pen.setStyle(Qt.PenStyle.DotLine)
        fwhm_pen.setCosmetic(True)
        self._fwhm_lines = [pg.InfiniteLine(angle=90, pen=fwhm_pen) for _ in range(2)]
        for line in self._fwhm_lines:
            line.setVisible(False)

        # RF frequency-range readout. Parented to the ViewBox rather than added as a plot
        # item so it stays pinned to the top-left corner in SCREEN coordinates: the y axis is
        # raw counts and rescales by ~50x between a dim and a bright frame, so a label placed
        # in data coordinates would drift off screen on the next shot.
        self._rf_label = pg.TextItem(color=QColor("white"), anchor=(0, 0))
        self._rf_label.setZValue(100)

        # Knife-edge markers: where the truncation detector put the clip, i.e. the boundary
        # of the data the committed fit actually rests on. Two lines, one per side; a frame
        # clipped on one side only ever shows one. Cosmetic pen for the same reason as the
        # curves above -- dash lengths must be in screen pixels, not data units.
        for side in ("left", "right"):
            pen = QPen(QColor("red"))
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            # Only the LEFT edge is draggable. It is the one the f_cfg readout quotes its
            # short-wavelength terminal at, so moving it means something; a handle on the
            # right would move under the cursor and change nothing, which is worse than no
            # handle at all.
            movable = side == "left"
            line = pg.InfiniteLine(angle=90, movable=movable, pen=pen,
                                   label="knife " + side,
                                   labelOpts={"color": "red", "position": 0.92,
                                              "movable": False})
            line.setZValue(50)
            line.setVisible(False)
            if movable:
                # On RELEASE, not on every mouse move: the readout re-renders and the value
                # crosses IPC into the persisted config, and doing that per pixel of drag
                # would push hundreds of config writes for one gesture.
                line.sigPositionChangeFinished.connect(self._on_knife_dragged)
            self._knife_lines.append(line)

    def set_active(self, active: bool) -> None:
        """Attach/detach the spectrum_fit overlay curves to the shared chart."""
        if active == self._active:
            return
        self._active = active
        if active:
            self._attach_curves()
            self._update_curves()
        else:
            self._detach_curves()

    @property
    def plot_frequency(self) -> bool:
        return self._plot_frequency

    def set_plot_frequency(self, enabled: bool) -> None:
        if enabled == self._plot_frequency:
            return
        self._plot_frequency = enabled
        self._update_curves()
        self.plot_mode_changed.emit(enabled)

    @property
    def show_knife_edges(self) -> bool:
        return self._show_knife_edges

    def set_show_knife_edges(self, enabled: bool) -> None:
        if enabled == self._show_knife_edges:
            return
        self._show_knife_edges = enabled
        self._update_knife_lines()
        self.knife_edges_changed.emit(enabled)

    def _attach_curves(self) -> None:
        if self._plot_item is None or self._set_phase_series is None or self._current_phase_series is None:
            return
        for series in (self._set_phase_series, self._current_phase_series,
                       self._template_series, self._mean_series):
            # Excluded from auto-range so the view stays driven by the live spectrum,
            # not by whatever the fit curves happen to be before a config is applied.
            if series is not None:
                self._plot_item.addItem(series, ignoreBounds=True)
        for line in (*self._fwhm_lines, *self._knife_lines):
            self._plot_item.addItem(line, ignoreBounds=True)
        if self._rf_label is not None:
            self._rf_label.setParentItem(self._plot_item.getViewBox())
            self._rf_label.setPos(10, 6)          # screen px inset from the top-left

    def _detach_curves(self) -> None:
        if self._plot_item is None:
            return
        for series in (self._set_phase_series, self._current_phase_series,
                       self._template_series, self._mean_series):
            if series is not None:
                self._plot_item.removeItem(series)
        for line in (*self._fwhm_lines, *self._knife_lines):
            self._plot_item.removeItem(line)
        if self._rf_label is not None:
            self._rf_label.setParentItem(None)

    def _update_curves(self, rescale: bool = False) -> None:
        if not self._active:
            return
        if self._set_phase_series is None or self._current_phase_series is None:
            return

        p = self._config.params
        lambda_ref = p.lambda_ref.value(Prefix.NANO)
        wl = np.linspace(
            self._config.wavelength_range.min.value(Prefix.NANO),
            self._config.wavelength_range.max.value(Prefix.NANO),
            300,
        )
        # Three curves, and each one is drawn ONLY while it means something. A curve on this
        # chart is read as a measurement; one drawn from defaults, or from a fit committed
        # minutes ago in another mode, is a wrong measurement rather than a harmless extra.
        #
        #   yellow  the frozen reference itself (drawn in _update_template_curve)
        #   red     the TARGET: fringes shifted so the phase at λ_ref equals set_phase
        #   green   the last committed cold fit -- where the fringes were measured to be
        #
        # ONE reference curve at a time, and its colour says where the reference came from:
        # RED for a captured one, YELLOW for a recalled one. There is only ever one reference
        # installed, so drawing two curves invited the reading that the loop was working
        # between them -- it never was.
        #
        # With no reference there is nothing to draw against: the target is only meaningful
        # relative to one, so red stays off until a reference exists. The exception is the
        # fast loop, which has no reference by design and corrects to the setpoint off each
        # frame's own fit -- there red IS the live target and green the live measurement.
        #
        # committed: FringeFitParams starts at all-zeros and ref_wl is only ever set by a
        # fit that passed the accept gate, so this is "has anything ever been fit".
        committed = p.ref_wl > 0.0
        current_phase_curve = None
        set_phase_curve = None
        reference_curve = self._reference_curve(wl)
        mean_curve = self._mean_curve(wl)
        if reference_curve is not None:
            if not self._pinned:
                set_phase_curve = reference_curve      # captured -> red
        elif committed and not self._config.slow_correction:
            mid, half, phase = display_curve(p.as_result(), wl)
            current_phase_curve = mid + half * np.cos(phase)
            set_shift = self._config.set_phase.Rad - p.phase_ref
            set_phase_curve = mid + half * np.cos(phase + set_shift)

        if self._plot_frequency:
            # Ω(λ) = 2π·c/λ − 2π·c/λ_ref (same detuning mapping as before, now
            # referenced to the fit's λ_ref instead of the old model's λ0).
            omega = 2.0 * np.pi * SPEED_OF_LIGHT / wl * 1e-3
            omega0 = 2.0 * np.pi * SPEED_OF_LIGHT / lambda_ref * 1e-3
            x = omega - omega0
        else:
            x = wl

        # The toggles bite HERE rather than on setVisible: a hidden pyqtgraph item still
        # holds its data and still reappears with a stale curve the moment it is shown
        # again, which is worse than no curve at all on a chart read as a measurement.
        if not self._show_target:
            set_phase_curve = reference_curve = None
        if not self._show_mean:
            mean_curve = None
        for series, curve in ((self._set_phase_series, set_phase_curve),
                              (self._current_phase_series, current_phase_curve),
                              (self._mean_series, mean_curve)):
            if curve is None:
                series.clear()
            else:
                series.setData(x, curve)
        # Yellow carries the recalled reference and nothing else; a captured one went into
        # the red series above.
        if reference_curve is not None and self._pinned:
            self._template_series.setData(x, reference_curve)
        else:
            self._template_series.clear()
        self._update_rf_label()
        self._update_fwhm_lines()
        self._update_knife_lines()

        if rescale and self._plot_item is not None:
            x_lo, x_hi = float(x.min()), float(x.max())
            x_pad = (x_hi - x_lo) * 0.1
            curves = [c for c in (set_phase_curve, current_phase_curve, mean_curve)
                      if c is not None]
            if not curves:
                return
            y_lo = float(min(c.min() for c in curves))
            y_hi = float(max(c.max() for c in curves))
            y_pad = (y_hi - y_lo) * 0.1 or 1.0
            self._plot_item.setXRange(x_lo - x_pad, x_hi + x_pad, padding=0)
            self._plot_item.setYRange(y_lo - y_pad, y_hi + y_pad, padding=0)

    def _reference_curve(self, wl: np.ndarray) -> np.ndarray | None:
        """The installed reference sampled on ``wl``, or None if there is no reference.

        Drawn as it was measured -- the frozen envelopes and phase polynomial, no setpoint
        shift. It is a record, and shifting it would make the chart show a shape that was
        never captured.
        """
        tpl = self._template
        if tpl is None or self._template_series is None:
            return None
        mid, half = tpl.envelopes(wl)
        return mid + half * np.cos(tpl.phi(wl))

    def _mean_curve(self, wl: np.ndarray) -> np.ndarray | None:
        """The reference shape shifted to the AVERAGED measured phase, or None.

        This is the trace the correction is computed from: the loop does not act on any one
        frame, it acts on the circular mean of every frame since the last correction. Drawing
        it lets the operator see the same gap the loop is about to close, instead of
        inferring it from a raw trace noisier than anything the plate will be told to do.

        Needs a reference: the mean is an absolute phase measured AGAINST the template's
        shape, so without one there is no shape to hang it on.
        """
        tpl = self._template
        if tpl is None or self._mean_series is None or self._mean_phase is None:
            return None
        lambda_ref = self._config.params.lambda_ref.value(Prefix.NANO)
        shift = self._mean_phase - float(tpl.phi(np.array([lambda_ref]))[0])
        mid, half = tpl.envelopes(wl)
        return mid + half * np.cos(tpl.phi(wl) + shift)

    def _on_mean(self, event: PhaseMeanReported) -> None:
        self._mean_phase = float(event.mean_phase_rad) if event.valid else None
        self._dispatcher.post(self._update_curves)

    # --- which curves are drawn ------------------------------------------------------
    @property
    def show_raw(self) -> bool:
        return self._show_raw

    @property
    def show_mean(self) -> bool:
        return self._show_mean

    @property
    def show_target(self) -> bool:
        return self._show_target

    def set_show_raw(self, enabled: bool) -> None:
        if enabled == self._show_raw:
            return
        self._show_raw = enabled
        # The live spectrum is drawn by the chart view, not by this model -- it is the one
        # curve here that is not a fit -- so the toggle travels out rather than being
        # applied alongside the other two.
        self.raw_visible_changed.emit(enabled)
        self.traces_changed.emit()

    def set_show_mean(self, enabled: bool) -> None:
        if enabled == self._show_mean:
            return
        self._show_mean = enabled
        self._update_curves()
        self.traces_changed.emit()

    def set_show_target(self, enabled: bool) -> None:
        if enabled == self._show_target:
            return
        self._show_target = enabled
        self._update_curves()
        self.traces_changed.emit()

    def _to_plot_x(self, wl_nm: float) -> float:
        """A wavelength in the plot's current x units.

        The knife edge is measured in nm, but the chart may be showing detuning, so the
        marker has to go through the SAME mapping as the curves -- otherwise it would sit at
        a plausible-looking but wrong place, which is worse than not drawing it.
        """
        if not self._plot_frequency:
            return float(wl_nm)
        lambda_ref = self._config.params.lambda_ref.value(Prefix.NANO)
        return float(2.0 * np.pi * SPEED_OF_LIGHT / wl_nm * 1e-3
                     - 2.0 * np.pi * SPEED_OF_LIGHT / lambda_ref * 1e-3)

    def _from_plot_x(self, x: float) -> float:
        """The inverse of :meth:`_to_plot_x`: a plot x back to a wavelength in nm.

        The detuning map is its own inverse in form -- omega = 2*pi*c/lambda - omega_ref
        rearranges to lambda = 2*pi*c/(x + omega_ref) -- so a drag in frequency mode lands
        on the same nm the marker was drawn from, with no accumulated error from a
        round trip.
        """
        if not self._plot_frequency:
            return float(x)
        lambda_ref = self._config.params.lambda_ref.value(Prefix.NANO)
        omega_ref = 2.0 * np.pi * SPEED_OF_LIGHT / lambda_ref * 1e-3
        omega = float(x) + omega_ref
        if omega <= 0.0:
            return float("nan")
        return float(2.0 * np.pi * SPEED_OF_LIGHT / omega * 1e-3)

    @property
    def effective_cut_left(self) -> float | None:
        """The short-wavelength terminal the readout quotes at, in nm, or None.

        The operator's dragged value wins over the fit's own detection when there is one.
        With no drag this is exactly the detected cut, so the default behaviour is
        unchanged -- which is the point: the override exists for the frames where the
        detector is wrong, not to become the normal path.
        """
        if self._config.manual_cut_left is not None:
            return float(self._config.manual_cut_left)
        cut = self._config.params.cut_left
        return None if cut is None else float(cut)

    @property
    def cut_left_is_manual(self) -> bool:
        return self._config.manual_cut_left is not None

    def _on_knife_dragged(self, line) -> None:
        """The operator moved the left knife edge: adopt it as the manual terminal."""
        nm = self._from_plot_x(float(line.value()))
        if not np.isfinite(nm):
            self._update_knife_lines()      # snap back rather than store a nonsense edge
            return
        self._config.manual_cut_left = nm
        self._handle.set_config(self._config)
        self._update_rf_label()
        self._update_knife_lines()
        self.cut_left_changed.emit(nm, True)

    def clear_manual_cut_left(self) -> None:
        """Hand the terminal back to the fit's own detection."""
        if self._config.manual_cut_left is None:
            return
        self._config.manual_cut_left = None
        self._handle.set_config(self._config)
        self._update_rf_label()
        self._update_knife_lines()
        cut = self.effective_cut_left
        self.cut_left_changed.emit(float("nan") if cut is None else cut, False)

    def _update_knife_lines(self) -> None:
        """Place/hide the two knife-edge markers from the committed fit.

        Hidden when the toggle is off, and hidden per side when that side has no cut -- an
        unclipped frame must show nothing at all rather than a marker parked at an edge of
        the window, which would read as a clip that is not there.
        """
        if not self._knife_lines:
            return
        p = self._config.params
        for line, cut in zip(self._knife_lines, (self.effective_cut_left, p.cut_right)):
            if cut is None or not self._show_knife_edges:
                line.setVisible(False)
                continue
            line.setPos(self._to_plot_x(float(cut)))
            # Re-shown explicitly: a side that was hidden on an unclipped frame has to come
            # back when the clip returns, and setPos alone does not do that.
            line.setVisible(True)

    def _update_fwhm_lines(self) -> None:
        """Place the two verticals at this shot's own FWHM edges.

        Same band the f_cfg readout quotes -- ``fringe_core.fwhm_band_nm`` on the committed
        envelope -- so the operator can see WHERE the two numbers in the label are taken.
        They are hidden, not left stale, whenever that band does not exist (un-fitted
        config, degenerate envelope): a marker from a previous shot would be read as a
        measurement of this one.

        In frequency mode the edges go through the same detuning map as the curves, which
        is monotonically DECREASING in wavelength, so the red edge ends up on the left. No
        reordering is needed -- these are two independent lines, not a span.
        """
        if not self._fwhm_lines:
            return
        p = self._config.params
        band = fc.fwhm_band_nm(p.pU) if any((p.c1, p.c2, p.c3)) else None
        if band is None:
            for line in self._fwhm_lines:
                line.setVisible(False)
            return
        for line, nm in zip(self._fwhm_lines, band):
            line.setPos(self._to_plot_x(float(nm)))
            line.setVisible(True)

    def _update_rf_label(self) -> None:
        """Show f_cfg at the two edges of this shot's own measured FWHM.

        The spectral fringe rate is converted through the dispersive time-mapping
        calibration in fringe_core (9 nm ~ 310 ps, linear => 29.032 GHz per cycle/nm) and
        halved: the centrifuge frequency is HALF the fringe beat (``CFG_PER_FRINGE``).

        The band comes from the fitted envelope's FWHM rather than a fixed 802 +- 9 nm
        window, so the readout stays inside the light that actually exists instead of
        extrapolating the cubic ~2.5x past the spectrum. The shape_ok gate still applies --
        the fitted core can be narrower than the FWHM -- so an unverified shape is labelled
        rather than quoted bare. An un-fitted config (c1 = c2 = c3 = 0) or a degenerate
        envelope shows nothing at all, rather than a "0 GHz" that would be a lie about a
        measurement that has not happened.
        """
        if self._rf_label is None:
            return
        p = self._config.params
        if not any((p.c1, p.c2, p.c3)):
            self._rf_label.setText("")
            return
        rng = fc.cfg_range((p.c0, p.c1, p.c2, p.c3), p.l0, p.pU,
                           cut_left=self.effective_cut_left)
        self._rf_label.setText(fc.format_cfg_range(rng, p.shape_ok))
        self._rf_label.setColor(QColor("white") if p.shape_ok else QColor("orange"))

    @property
    def worker_state(self) -> WorkerStatus:
        return self._handle.state

    @property
    def config(self) -> StabilizationConfig:
        return self._config

    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def resume(self) -> None:
        self._handle.resume()

    def stop(self) -> None:
        self._handle.stop()

    def apply(self, set_phase_deg: float) -> None:
        """Commit pending values into the shared config and send to the subprocess."""
        self._config.set_phase = Angle(set_phase_deg, AngleUnit.DEG)
        self._handle.set_config(self._config)
        self._update_curves(rescale=True)

    def _on_state_changed(self, _: PhaseTrackingStateChanged) -> None:
        state = self._handle.state
        self._dispatcher.post(lambda: self.worker_state_changed.emit(state))

    def _on_config_updated(self, _: StabilizationConfigChanged) -> None:
        def _apply() -> None:
            self._update_curves()
            self.config_updated.emit()

        self._dispatcher.post(_apply)

    # ------------------------------------------------------------------ frozen template --
    @property
    def template_text(self) -> str:
        return self._template_text

    @property
    def has_template(self) -> bool:
        return self._handle.template is not None

    @property
    def slow_correction(self) -> bool:
        return self._config.slow_correction

    def set_slow_correction(self, slow: bool) -> None:
        """Switch between the frozen-template loop and the cold per-frame one.

        Pushed straight to the subprocess rather than waiting for Apply: this is a mode
        switch, not a tuning value, and an operator reaching for Fast because the loop is
        misbehaving wants it now.
        """
        if bool(slow) == self._config.slow_correction:
            return
        self._config.slow_correction = bool(slow)
        self._handle.set_config(self._config)

    # --- waveplate readout ---------------------------------------------------------
    @property
    def plate_text(self) -> str:
        return self._plate_text

    def _on_plate_angle(self, event: NewRGVAngle) -> None:
        self._plate_deg = float(event.angle.Deg)
        self._dispatcher.post(self._refresh_plate_text)

    def _on_correction(self, event: PhaseCorrectionReported) -> None:
        self._period_s = float(event.period_s) or self._period_s
        self._next_at = time.monotonic() + self._period_s
        if event.applied:
            self._last_command = (f"{event.commanded_deg:+.3f}° "
                                  f"(error {event.phase_error_rad:+.3f} rad, "
                                  f"{event.frames} frames)")
        else:
            # "held" is a real outcome, not a missing reading: inside the deadband the loop
            # is working exactly as configured, and saying nothing would read as a fault.
            self._last_command = f"held — {event.reason}" if event.reason else "held"
        self._dispatcher.post(self._refresh_plate_text)

    def _refresh_plate_text(self) -> None:
        angle = "—" if self._plate_deg is None else f"{self._plate_deg:.3f}°"
        if self._next_at is None:
            nxt = "—"
        else:
            # Clamped at zero: the instant is evaluated on the next accepted frame, so a
            # loop waiting on the spectrometer would otherwise count into the negatives.
            nxt = f"{max(self._next_at - time.monotonic(), 0.0):.0f} s"
        text = (f"Waveplate: {angle}    last command: {self._last_command}"
                f"    next: {nxt}")
        if text == self._plate_text:
            return
        self._plate_text = text
        self.plate_status_changed.emit(text)

    @property
    def has_recall(self) -> bool:
        """A recalled template is pinned: nothing automatic will replace it."""
        return self._pinned

    def capture_reference(self) -> None:
        # Capture is the operator asking for a fresh shape, which retires the pinned recall
        # on the subprocess side -- so the panel must stop claiming one is held.
        self._set_recall(None)
        self._handle.capture_reference()

    def save_reference(self, path: str) -> bool:
        """Write the installed template to ``path``. False if there is nothing to write."""
        tpl = self._handle.template
        if tpl is None:
            return False
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(tpl.to_primitive(), fh, indent=2)
        return True

    def recall_reference(self, path: str) -> None:
        """Load a template from ``path``, show it, and pin it.

        The curve is drawn from the FILE, here, before the request goes out: recall is
        normally pressed with stabilization stopped, and in that case the subprocess has no
        tracker to install it into and nothing would ever echo back a shape to plot.
        """
        with open(path, encoding="utf-8") as fh:
            tpl = PhaseTemplate.from_primitive(json.load(fh))
        self._set_recall(tpl)
        self._handle.recall_reference(tpl)

    def clear_recall(self) -> None:
        """Deselect the recalled template: the loop may capture its own again."""
        if not self._pinned:
            return
        self._set_recall(None)
        self._handle.recall_reference(None)

    def _set_recall(self, tpl: PhaseTemplate | None) -> None:
        self._pinned = tpl is not None
        self._template = tpl
        if tpl is not None:
            self._template_text = (
                f"Reference: recalled and held — captured {tpl.captured_utc or '?'}, "
                f"{tpl.integration_ms:.0f} ms x{tpl.averages}")
            self.template_state_changed.emit(self._template_text)
        self._update_curves()
        self.recall_changed.emit(self._pinned)

    def _on_template_changed(self, event: PhaseTemplateChanged) -> None:
        # The drawn shape follows the LOOP. Once the subprocess is running it is the
        # authority on what is installed, including whether it is still a recall -- so a
        # capture that lands (or a drop) replaces the recalled curve rather than leaving the
        # file on the chart next to a reference that superseded it. Only "locked" carries a
        # template; every other state means there is nothing installed to draw.
        if event.state == "locked" and event.template is not None:
            self._template = event.template
            self._pinned = bool(event.pinned)
        else:
            self._template = None
            self._pinned = False
        self._dispatcher.post(self._update_curves)
        self._dispatcher.post(lambda: self.recall_changed.emit(self._pinned))
        if event.pinned and event.template is not None:
            text = (f"Reference: recalled and held — captured {event.template.captured_utc}, "
                    f"{event.template.integration_ms:.0f} ms x{event.template.averages}")
            self._template_text = text
            self._dispatcher.post(lambda: self.template_state_changed.emit(text))
            return
        if event.state == "capturing":
            text = f"Reference: capturing {event.captured}/{event.needed} — holding"
            if event.abandoned:
                # Said out loud because the counter falling back to 0 looks the same whether
                # the run was broken by one bad frame or the whole averaged run was rejected
                # -- and the second one repeating is a sign the fringes are not good enough
                # to capture from, which no amount of waiting will fix.
                text += (f" ({event.abandoned} rejected run"
                         f"{'s' if event.abandoned > 1 else ''}, retrying)")
        elif event.state == "idle":
            # Named for what the operator has to DO about it. The loop is running and
            # measuring; it just has no shape to correct against, and it will not go and get
            # one on its own -- that is now always an explicit act.
            text = "Reference: none — holding. Press Capture reference."
        elif event.state == "locked" and event.template is not None:
            text = (f"Reference: locked — captured {event.template.captured_utc}, "
                    f"{event.template.integration_ms:.0f} ms x{event.template.averages}")
        elif event.state == "locked":
            text = "Reference: locked"
        else:
            text = _TEMPLATE_OFF_TEXT
        self._template_text = text
        self._dispatcher.post(lambda: self.template_state_changed.emit(text))
