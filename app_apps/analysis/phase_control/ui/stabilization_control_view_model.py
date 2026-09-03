from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPen

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_core.math.enums import AngleUnit
from base_core.math.models import Angle
from base_core.quantities.constants import SPEED_OF_LIGHT
from base_core.quantities.enums import Prefix
from base_qt.app.dispatcher import QtDispatcher
from app_apps.analysis.phase_control.events import (
    PhaseBatchChanged,
    PhaseTrackingStateChanged,
    StabilizationConfigChanged,
)
from app_apps.io.control_readout.rgv.events import NewRGVAngle, RequestRotateRGV
from app_apps.io.spectrometer.events import SpectrometerConfigChanged
from app_apps.analysis.phase_control.subprocess.domain import fringe_core as fc
from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import display_curve

# Shown before the worker has said anything. The loop starts by capturing, so this is what
# is true at that moment rather than a placeholder.
_IDLE_TEXT = "Reference: not captured"

if TYPE_CHECKING:
    from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
    from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig


class StabilizationControlViewModel(QObject):
    worker_state_changed = Signal(object)  # WorkerStatus
    config_updated = Signal()              # subprocess synced new fit params
    plot_mode_changed = Signal(bool)       # plot-in-frequency toggled
    loop_state_changed = Signal(str)       # human-readable capture / averaging state
    knife_edges_changed = Signal(bool)     # knife-edge markers toggled
    raw_visible_changed = Signal(bool)     # the live spectrum curve, which this VM does not
                                           # own -- PhaseControlView draws it, so the toggle
                                           # has to travel out rather than act here
    cut_left_changed = Signal(float, bool)  # terminal nm, True when manually set
    roi_changed = Signal(bool)             # an ROI is (or is no longer) in force. The panel
                                           # reads this to enable "Auto"/"Zoom to ROI" and to
                                           # say, in words, that the quality gates are off.
    avg_visible_changed = Signal(bool)     # the running-average curve, drawn by
                                           # PhaseControlView for the same reason Raw is
    block_reset = Signal()                 # a new averaging block started: the running
                                           # average must start over with it, or it would
                                           # smear frames from either side of a correction
    correction_issued = Signal(float)      # commanded plate increment, deg
    readout_changed = Signal()             # plate angle / last correction / countdown

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
        self._roi_lines: list[pg.InfiniteLine] = []
        self._roi_last: tuple[float, float] | None = None
        self._zoomed_to_roi = False   # so dropping the ROI can undo the zoom
        self._active = False
        self._plot_frequency = False
        self._show_knife_edges = False   # off by default: two more lines on a busy chart
        self._unsub = bus.subscribe(PhaseTrackingStateChanged, self._on_state_changed)
        self._unsub_cfg = bus.subscribe(StabilizationConfigChanged, self._on_config_updated)
        self._unsub_batch = bus.subscribe(PhaseBatchChanged, self._on_batch_changed)
        self._loop_text = _IDLE_TEXT
        # Raw, the running average and the target are on; the per-frame Fit is OFF. The fit
        # curve redraws on every accepted frame and is the noisiest thing on the chart, and
        # the loop does not correct on any single frame anyway -- the average is what it acts
        # on, so that is what the panel shows by default.
        self._show_raw = True
        self._show_fit = False
        self._show_target = True
        self._show_avg = True

        # Readouts. None means "not known yet" and is rendered as a dash rather than a
        # zero -- 0.00 deg is a legitimate plate angle and a legitimate correction, so a
        # placeholder that looks like one would be unreadable.
        self._waveplate_deg: float | None = None
        self._last_correction_deg: float | None = None
        self._remaining: int | None = None
        self._settling = False
        self._capturing = False
        self._error_deg = float("nan")
        self._collected = 0
        # RequestRotateRGV is what THIS loop asks the plate to do, published by
        # PhaseStabilizationHandle the moment a correction lands, so it is both the "most
        # recent correction" readout and the earliest signal that a block just ended.
        self._unsub_rot = bus.subscribe(RequestRotateRGV, self._on_rotate_requested)
        self._unsub_rgv = bus.subscribe(NewRGVAngle, self._on_rgv_angle)
        # Exposure and averaging change the COUNTS. Frames from either side of that change
        # cannot go into one mean -- the average would sit at some blend of the two
        # amplitudes, which reads as a real change in the light and is not one.
        self._unsub_spec = bus.subscribe(SpectrometerConfigChanged, self._on_spectrometer_config)

    def set_chart(self, plot_item: pg.PlotItem) -> None:
        self._plot_item = plot_item

        # Cosmetic so dash lengths are in screen pixels, not data units — without it,
        # the wavelength (nm) vs intensity (0-1) axes' mismatched scale stretches each
        # dash into a long diagonal streak, making the curve look like jagged noise.
        set_phase_pen = QPen(QColor("red"))
        set_phase_pen.setStyle(Qt.PenStyle.DashLine)
        set_phase_pen.setCosmetic(True)
        self._set_phase_series = pg.PlotDataItem(pen=set_phase_pen)

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

        # ROI bounds -- the analysis region, asserted by the operator. GREEN and SOLID, and
        # deliberately unlike everything else on the chart: the knife edges are red dashed
        # (where the fit says the data ran out) and the FWHM markers cyan dotted (where the
        # readout is quoted). This pair is the only one that CHANGES THE FIT, so it must not
        # be mistakable for either. Hidden entirely when there is no ROI -- a pair of bounds
        # parked at the window edges would read as an ROI that is not there.
        for _ in ("lo", "hi"):
            pen = QPen(QColor("#39d353"))
            pen.setCosmetic(True)
            line = pg.InfiniteLine(angle=90, movable=True, pen=pen, label="ROI",
                                   labelOpts={"color": "#39d353", "position": 0.08,
                                              "movable": False})
            line.setZValue(60)
            line.setVisible(False)
            # On RELEASE, like the knife edge: the value crosses IPC into the persisted
            # config AND reconfigures the fit, so a write per pixel of drag would re-tune the
            # running loop hundreds of times for one gesture.
            line.sigPositionChangeFinished.connect(self._on_roi_dragged)
            self._roi_lines.append(line)

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

    # --- trace visibility --------------------------------------------------------------
    @property
    def show_raw(self) -> bool:
        return self._show_raw

    def set_show_raw(self, enabled: bool) -> None:
        self._show_raw = bool(enabled)
        self.raw_visible_changed.emit(self._show_raw)

    @property
    def show_fit(self) -> bool:
        return self._show_fit

    def set_show_fit(self, enabled: bool) -> None:
        self._show_fit = bool(enabled)
        self._update_curves()

    @property
    def show_target(self) -> bool:
        return self._show_target

    def set_show_target(self, enabled: bool) -> None:
        self._show_target = bool(enabled)
        self._update_curves()

    @property
    def show_avg(self) -> bool:
        return self._show_avg

    def set_show_avg(self, enabled: bool) -> None:
        self._show_avg = bool(enabled)
        self.avg_visible_changed.emit(self._show_avg)

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
        for series in (self._set_phase_series, self._current_phase_series):
            # Excluded from auto-range so the view stays driven by the live spectrum,
            # not by whatever the fit curves happen to be before a config is applied.
            self._plot_item.addItem(series, ignoreBounds=True)
        for line in (*self._fwhm_lines, *self._knife_lines, *self._roi_lines):
            self._plot_item.addItem(line, ignoreBounds=True)
        if self._rf_label is not None:
            self._rf_label.setParentItem(self._plot_item.getViewBox())
            self._rf_label.setPos(10, 6)          # screen px inset from the top-left

    def _detach_curves(self) -> None:
        if self._plot_item is None:
            return
        for series in (self._set_phase_series, self._current_phase_series):
            if series is not None:
                self._plot_item.removeItem(series)
        for line in (*self._fwhm_lines, *self._knife_lines, *self._roi_lines):
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
        # Reconstruct the committed cubic-phase fringe: current = mid+half·cos(Φ),
        # set = same envelopes/chirp shifted so the phase at λ_ref equals set_phase.
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

        # The toggles bite HERE rather than on setVisible: a hidden pyqtgraph item keeps its
        # data and reappears with a stale curve the moment it is shown again, which is worse
        # than no curve at all on a chart that is read as a measurement.
        if self._show_target:
            self._set_phase_series.setData(x, set_phase_curve)
        else:
            self._set_phase_series.clear()
        if self._show_fit:
            self._current_phase_series.setData(x, current_phase_curve)
        else:
            self._current_phase_series.clear()
        self._update_rf_label()
        self._update_fwhm_lines()
        self._update_knife_lines()
        self._update_roi_lines()

        if rescale and self._plot_item is not None:
            x_lo, x_hi = float(x.min()), float(x.max())
            x_pad = (x_hi - x_lo) * 0.1
            y_lo = float(min(set_phase_curve.min(), current_phase_curve.min()))
            y_hi = float(max(set_phase_curve.max(), current_phase_curve.max()))
            y_pad = (y_hi - y_lo) * 0.1 or 1.0
            self._plot_item.setXRange(x_lo - x_pad, x_hi + x_pad, padding=0)
            self._plot_item.setYRange(y_lo - y_pad, y_hi + y_pad, padding=0)

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

    # ------------------------------------------------------------------------- ROI --
    @property
    def roi(self) -> tuple[float, float] | None:
        """The analysis region in nm, or None for auto."""
        return self._config.roi

    @property
    def window_nm(self) -> tuple[float, float]:
        """The analysis window in nm -- the widest an ROI is allowed to be.

        Exposed so the panel can bound a typed ROI to it. An ROI outside the window is not
        a wider region, it is samples the fit never sees.
        """
        return (float(self._config.wavelength_range.min.value(Prefix.NANO)),
                float(self._config.wavelength_range.max.value(Prefix.NANO)))

    def set_roi(self, lo_nm: float, hi_nm: float) -> None:
        """Assert the analysis region. This CHANGES THE FIT -- see StabilizationConfig.roi.

        Ordered here rather than trusted from the caller: the two bounds are independent
        draggable lines and nothing stops the operator pulling one past the other.
        """
        lo, hi = sorted((float(lo_nm), float(hi_nm)))
        if not (np.isfinite(lo) and np.isfinite(hi)) or hi - lo <= 0.0:
            self._update_roi_lines()      # snap back rather than store a degenerate ROI
            return
        self._config.roi_lo, self._config.roi_hi = lo, hi
        self._push_roi()

    def clear_roi(self) -> None:
        """Back to Auto: the full pipeline, every guard, exactly as it was before the ROI."""
        if self._config.roi_lo is None and self._config.roi_hi is None:
            return
        self._roi_last = self._config.roi          # so re-ticking the box restores it
        self._config.roi_lo = self._config.roi_hi = None
        self._push_roi()

    def set_roi_enabled(self, enabled: bool) -> None:
        """The whole ROI control: one switch, on or off.

        Turning it ON restores the bounds you last used, or -- the first time -- takes them
        from what is on screen, so the gesture is "zoom to the fringes you trust, tick the
        box". Either way the bounds are then draggable; turning it OFF is the way back.
        """
        if enabled == (self._config.roi is not None):
            return
        if not enabled:
            self.clear_roi()
            return
        if self._roi_last is not None:
            self.set_roi(*self._roi_last)
        else:
            self.set_roi_from_view()

    def _push_roi(self) -> None:
        # Dropping the ROI while the chart is framed on it would leave the operator staring
        # at 2 nm of a fit that is once again using the whole window -- a view that says
        # the opposite of what is happening. Zoom is view-only in both directions, so
        # undoing it here costs nothing and keeps the picture honest.
        if self._config.roi is None and self._zoomed_to_roi:
            self._zoom_window()
        self._handle.set_config(self._config)
        self._update_roi_lines()
        self.roi_changed.emit(self._config.roi is not None)

    def set_roi_from_view(self) -> None:
        """Take the ROI from what is currently on screen, clipped to the analysis window.

        "Zoom to the fringes you trust, then say so" is the gesture this exists for, and it
        is the only place the view and the fit are allowed to meet -- zoom itself never
        touches the fit, and this is an explicit command, not a side effect of panning.
        """
        if self._plot_item is None:
            return
        (x_lo, x_hi), _ = self._plot_item.getViewBox().viewRange()
        lo, hi = sorted((self._from_plot_x(x_lo), self._from_plot_x(x_hi)))
        w_lo = self._config.wavelength_range.min.value(Prefix.NANO)
        w_hi = self._config.wavelength_range.max.value(Prefix.NANO)
        self.set_roi(max(lo, w_lo), min(hi, w_hi))

    def zoom_to_roi(self) -> None:
        """View only. Sets the chart's x limits and reaches nothing else.

        With no ROI set this frames the analysis window, which is the useful thing to do
        with a "zoom" button when there is no region to zoom to.
        """
        if self._plot_item is None:
            return
        roi = self._config.roi
        if roi is None:
            lo = self._config.wavelength_range.min.value(Prefix.NANO)
            hi = self._config.wavelength_range.max.value(Prefix.NANO)
        else:
            lo, hi = roi
        pad = (hi - lo) * 0.15
        x0, x1 = sorted((self._to_plot_x(lo - pad), self._to_plot_x(hi + pad)))
        self._plot_item.setXRange(x0, x1, padding=0)
        # Y is left to autorange: the counts scale by ~50x between a dim and a bright frame,
        # so a y range carried over from the wide view would put the fringes off screen.
        self._plot_item.enableAutoRange(axis="y")
        self._zoomed_to_roi = roi is not None

    def _zoom_window(self) -> None:
        """View only: back to the configured analysis window."""
        if self._plot_item is None:
            return
        lo = self._config.wavelength_range.min.value(Prefix.NANO)
        hi = self._config.wavelength_range.max.value(Prefix.NANO)
        x0, x1 = sorted((self._to_plot_x(lo), self._to_plot_x(hi)))
        self._plot_item.setXRange(x0, x1, padding=0.02)
        self._plot_item.enableAutoRange(axis="y")
        self._zoomed_to_roi = False

    def _on_roi_dragged(self, _line) -> None:
        """Either bound moved: read BOTH back, so a bound dragged past the other still
        yields an ordered ROI rather than an empty one."""
        lo, hi = (self._from_plot_x(float(ln.value())) for ln in self._roi_lines)
        self.set_roi(lo, hi)

    def _update_roi_lines(self) -> None:
        if not self._roi_lines:
            return
        roi = self._config.roi
        if roi is None:
            for line in self._roi_lines:
                line.setVisible(False)
            return
        for line, nm in zip(self._roi_lines, roi):
            line.setPos(self._to_plot_x(float(nm)))
            line.setVisible(True)

    # ------------------------------------------------------------------- quality --
    @property
    def quality_text(self) -> str:
        """The three gates the ROI turns off, as a readout.

        They are computed either way -- the fit already paid for them -- so with an ROI set
        they are shown instead of enforced. The operator took the judgement when they drew
        the region; this is what they judge with. Under Auto the same numbers are still the
        honest description of the committed fit, so it is shown in both modes.
        """
        p = self._config.params
        if not any((p.c1, p.c2, p.c3)):
            return "Fit: —"
        gated = "advisory" if self._config.roi is not None else "gating"
        trust = "trust ✓" if p.trust_ok else "trust ✗"
        return (f"Fit: {trust}  rms/amp {p.rms_frac:.3f}  inliers {p.inlier_pct:.0f}%"
                f"  ({gated})")

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

    # --------------------------------------------------------------------- block loop --
    @property
    def loop_text(self) -> str:
        return self._loop_text

    def capture_target(self) -> None:
        """Refit every parameter cold and re-zero the phase against the result."""
        self._handle.capture_target()

    def _on_batch_changed(self, event: PhaseBatchChanged) -> None:
        # The block restarting is what the running-average curve is keyed off: the count
        # going backwards (or to zero) means the frames behind it were consumed by a
        # correction, or thrown away by a pause / stage move. Either way the next average
        # must not include them.
        if event.collected < self._collected:
            self._dispatcher.post(self.block_reset.emit)
        self._collected = event.collected
        self._capturing = event.capturing
        self._settling = event.settling
        self._error_deg = float(event.error_deg)
        self._remaining = None if event.capturing else max(event.needed - event.collected, 0)

        if event.capturing:
            text = f"Reference: capturing {event.collected}/{event.needed} — holding"
        elif event.settling:
            text = "Averaging: waiting for the plate to settle"
        else:
            # The error, not the agreement. "agreement 0.97" says the fits concur; it does
            # not say whether they concur about being on target, which is the only question
            # the operator is actually asking of this line.
            off = ("—" if not np.isfinite(self._error_deg)
                   else f"{self._error_deg:+.2f}° off")
            text = f"Averaging {event.collected}/{event.needed} — {off}"
        self._loop_text = text

        def _emit() -> None:
            self.loop_state_changed.emit(text)
            self.readout_changed.emit()

        self._dispatcher.post(_emit)

    # ---------------------------------------------------------------------- readouts --
    @property
    def error_deg(self) -> float:
        """The block's running error against the setpoint, in degrees. NaN when unknown."""
        return self._error_deg

    @property
    def waveplate_deg(self) -> float | None:
        return self._waveplate_deg

    @property
    def last_correction_deg(self) -> float | None:
        return self._last_correction_deg

    @property
    def countdown_text(self) -> str:
        """Frames still needed before the next correction can be issued.

        Counted in FRAMES rather than seconds on purpose: the loop has no clock. It corrects
        when the block fills, so a frame count is the real remaining distance, and it stalls
        exactly when the fits stop being accepted -- which a seconds countdown would hide by
        continuing to tick.
        """
        if self._capturing:
            return "Next: capturing"
        if self._settling:
            return "Next: settling"
        if self._remaining is None:
            return "Next: —"
        return f"Next: {self._remaining} frame{'' if self._remaining == 1 else 's'}"

    def _on_rotate_requested(self, event: RequestRotateRGV) -> None:
        deg = float(event.angle.Deg)

        def _emit() -> None:
            self._last_correction_deg = deg
            # The plate is moving now, so the frames either side of this must not be
            # averaged together on the chart any more than they are in the loop.
            self.block_reset.emit()
            self.correction_issued.emit(deg)
            self.readout_changed.emit()

        self._dispatcher.post(_emit)

    def _on_spectrometer_config(self, _: SpectrometerConfigChanged) -> None:
        self._dispatcher.post(self.block_reset.emit)

    def _on_rgv_angle(self, event: NewRGVAngle) -> None:
        deg = float(event.angle.Deg)

        def _emit() -> None:
            self._waveplate_deg = deg
            self.readout_changed.emit()

        self._dispatcher.post(_emit)
