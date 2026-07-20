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
from app_apps.analysis.phase_control.events import PhaseTrackingStateChanged, StabilizationConfigChanged
from app_apps.analysis.phase_control.subprocess.domain import fringe_core as fc
from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import display_curve

if TYPE_CHECKING:
    from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
    from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig


class StabilizationControlViewModel(QObject):
    worker_state_changed = Signal(object)  # WorkerStatus
    config_updated = Signal()              # subprocess synced new fit params
    plot_mode_changed = Signal(bool)       # plot-in-frequency toggled
    set_phase_curve_changed = Signal(bool)  # red set-phase overlay toggled

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
        self._knife_lines: list[pg.InfiniteLine] = []
        self._active = False
        self._plot_frequency = False
        self._show_set_phase = True
        self._unsub = bus.subscribe(PhaseTrackingStateChanged, self._on_state_changed)
        self._unsub_cfg = bus.subscribe(StabilizationConfigChanged, self._on_config_updated)

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

        # RF frequency-range readout. Parented to the ViewBox rather than added as a plot
        # item so it stays pinned to the top-left corner in SCREEN coordinates: the y axis is
        # raw counts and rescales by ~50x between a dim and a bright frame, so a label placed
        # in data coordinates would drift off screen on the next shot.
        self._rf_label = pg.TextItem(color=QColor("white"), anchor=(0, 0))
        self._rf_label.setZValue(100)

        # Knife-edge markers: where the clip was CUT, i.e. the boundary of the data the
        # committed fit actually rests on. Two lines, one per side; a one-sided clip shows
        # one, an unclipped frame shows none. Cosmetic pen for the same reason as the curves
        # above -- dot spacing must be in screen pixels, not data units.
        #
        # ORANGE DOTTED, deliberately not red: red is the set-phase curve, which the operator
        # can switch off independently, and two different red things on one chart is how a
        # marker gets mistaken for part of the trace. Orange already means "the fit is
        # reaching past what it measured" here (the RF readout uses it for an unverified
        # shape), which is the same kind of statement a knife edge makes.
        for side in ("left", "right"):
            pen = QPen(QColor("orange"))
            pen.setStyle(Qt.PenStyle.DotLine)
            pen.setCosmetic(True)
            line = pg.InfiniteLine(angle=90, movable=False, pen=pen,
                                   label="knife " + side,
                                   labelOpts={"color": "orange", "position": 0.92,
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
    def show_set_phase(self) -> bool:
        return self._show_set_phase

    def set_show_set_phase(self, enabled: bool) -> None:
        """Show/hide the RED set-phase overlay -- the target the loop is driving toward.

        It is the busiest thing on the chart (a full oscillating reconstruction), and when
        the lock is close it sits on top of the green current-phase curve and the live
        spectrum, hiding both. Hiding it is a display choice only: nothing here touches the
        fit, the config or the loop.
        """
        if enabled == self._show_set_phase:
            return
        self._show_set_phase = enabled
        if self._set_phase_series is not None:
            self._set_phase_series.setVisible(enabled)
        self.set_phase_curve_changed.emit(enabled)

    def _attach_curves(self) -> None:
        if self._plot_item is None or self._set_phase_series is None or self._current_phase_series is None:
            return
        for series in (self._set_phase_series, self._current_phase_series):
            # Excluded from auto-range so the view stays driven by the live spectrum,
            # not by whatever the fit curves happen to be before a config is applied.
            self._plot_item.addItem(series, ignoreBounds=True)
        for line in self._knife_lines:
            self._plot_item.addItem(line, ignoreBounds=True)
        # Re-attaching builds fresh items; carry the operator's choice across.
        self._set_phase_series.setVisible(self._show_set_phase)
        if self._rf_label is not None:
            self._rf_label.setParentItem(self._plot_item.getViewBox())
            self._rf_label.setPos(10, 6)          # screen px inset from the top-left

    def _detach_curves(self) -> None:
        if self._plot_item is None:
            return
        for series in (self._set_phase_series, self._current_phase_series):
            if series is not None:
                self._plot_item.removeItem(series)
        for line in self._knife_lines:
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
        """Place the knife-edge markers: whichever edge the fit ACTUALLY CUT ON, and only that.

        Not toggleable, and not drawn speculatively. `cut_left`/`cut_right` arrive already
        filtered through `fringe_core.applied_cuts`, which is the same rule the fit itself
        uses to decide what to exclude -- so a side shows a marker if and only if the
        committed answer stands on data bounded there. An unclipped frame, or the unclipped
        side of a one-sided clip, draws nothing: a marker parked at the window edge would
        read as a clip that is not there.
        """
        if not self._knife_lines:
            return
        p = self._config.params
        for line, cut in zip(self._knife_lines, (p.cut_left, p.cut_right)):
            if cut is None:
                line.setVisible(False)
                continue
            line.setPos(self._to_plot_x(float(cut)))
            line.setVisible(True)

    def _update_rf_label(self) -> None:
        """Show the RF frequency range this shot generates, over 802 +- 9 nm.

        The spectral fringe rate is converted through the dispersive time-mapping
        calibration in fringe_core (9 nm ~ 320 ps, linear => 28.125 GHz per cycle/nm). The
        band is quoted wider than the fitted core on purpose, so this EXTRAPOLATES the cubic
        -- which is why an unverified shape is labelled rather than quoted bare. An
        un-fitted config (c1 = c2 = c3 = 0) would read "0.0 GHz", which is a lie about a
        measurement that has not happened, so it shows nothing at all instead.
        """
        if self._rf_label is None:
            return
        p = self._config.params
        if not any((p.c1, p.c2, p.c3)):
            self._rf_label.setText("")
            return
        lo, hi = fc.rf_range_ghz((p.c0, p.c1, p.c2, p.c3), p.l0)
        self._rf_label.setText(fc.format_rf_range(lo, hi, p.shape_ok))
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
