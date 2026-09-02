from __future__ import annotations

import json
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
    PhaseTemplateChanged,
    PhaseTrackingStateChanged,
    StabilizationConfigChanged,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_template import PhaseTemplate
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
        self._active = False
        self._plot_frequency = False
        self._show_knife_edges = True
        self._unsub = bus.subscribe(PhaseTrackingStateChanged, self._on_state_changed)
        self._unsub_cfg = bus.subscribe(StabilizationConfigChanged, self._on_config_updated)
        self._unsub_tpl = bus.subscribe(PhaseTemplateChanged, self._on_template_changed)
        self._template_text = _TEMPLATE_OFF_TEXT

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
            line = pg.InfiniteLine(angle=90, movable=False, pen=pen,
                                   label="knife " + side,
                                   labelOpts={"color": "red", "position": 0.92,
                                              "movable": False})
            line.setZValue(50)
            line.setVisible(False)
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
        for series in (self._set_phase_series, self._current_phase_series):
            # Excluded from auto-range so the view stays driven by the live spectrum,
            # not by whatever the fit curves happen to be before a config is applied.
            self._plot_item.addItem(series, ignoreBounds=True)
        for line in (*self._fwhm_lines, *self._knife_lines):
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

        self._set_phase_series.setData(x, set_phase_curve)
        self._current_phase_series.setData(x, current_phase_curve)
        self._update_rf_label()
        self._update_fwhm_lines()
        self._update_knife_lines()

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

    def _update_knife_lines(self) -> None:
        """Place/hide the two knife-edge markers from the committed fit.

        Hidden when the toggle is off, and hidden per side when that side has no cut -- an
        unclipped frame must show nothing at all rather than a marker parked at an edge of
        the window, which would read as a clip that is not there.
        """
        if not self._knife_lines:
            return
        p = self._config.params
        for line, cut in zip(self._knife_lines, (p.cut_left, p.cut_right)):
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
        rng = fc.cfg_range((p.c0, p.c1, p.c2, p.c3), p.l0, p.pU)
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

    def capture_reference(self) -> None:
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
        """Load a template from ``path`` and install it, overriding the current one."""
        with open(path, encoding="utf-8") as fh:
            self._handle.recall_reference(PhaseTemplate.from_primitive(json.load(fh)))

    def _on_template_changed(self, event: PhaseTemplateChanged) -> None:
        if event.state == "capturing":
            text = f"Reference: capturing {event.captured}/{event.needed} — holding"
        elif event.state == "locked" and event.template is not None:
            text = (f"Reference: locked — captured {event.template.captured_utc}, "
                    f"{event.template.integration_ms:.0f} ms x{event.template.averages}")
        elif event.state == "locked":
            text = "Reference: locked"
        else:
            text = _TEMPLATE_OFF_TEXT
        self._template_text = text
        self._dispatcher.post(lambda: self.template_state_changed.emit(text))
