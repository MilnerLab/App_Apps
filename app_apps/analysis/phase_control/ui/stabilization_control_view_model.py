from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPen

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_core.math.enums import AngleUnit
from base_core.math.functions import spectrum_fit_skew
from base_core.math.models import Angle
from base_core.quantities.constants import SPEED_OF_LIGHT
from base_core.quantities.enums import Prefix
from base_qt.app.dispatcher import QtDispatcher
from app_apps.analysis.phase_control.events import PhaseTrackingStateChanged, StabilizationConfigChanged

if TYPE_CHECKING:
    from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
    from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig


class StabilizationControlViewModel(QObject):
    worker_state_changed = Signal(object)  # WorkerStatus
    config_updated = Signal()              # subprocess synced new fit params
    plot_mode_changed = Signal(bool)       # plot-in-frequency toggled

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
        self._active = False
        self._plot_frequency = False
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

    def _attach_curves(self) -> None:
        if self._plot_item is None or self._set_phase_series is None or self._current_phase_series is None:
            return
        for series in (self._set_phase_series, self._current_phase_series):
            # Excluded from auto-range so the view stays driven by the live spectrum,
            # not by whatever the fit curves happen to be before a config is applied.
            self._plot_item.addItem(series, ignoreBounds=True)

    def _detach_curves(self) -> None:
        if self._plot_item is None:
            return
        for series in (self._set_phase_series, self._current_phase_series):
            if series is not None:
                self._plot_item.removeItem(series)

    def _update_curves(self, rescale: bool = False) -> None:
        if not self._active:
            return
        if self._set_phase_series is None or self._current_phase_series is None:
            return

        p = self._config.params
        wl = np.linspace(
            self._config.wavelength_range.min.value(Prefix.NANO),
            self._config.wavelength_range.max.value(Prefix.NANO),
            300,
        )
        lambda0 = p.lambda0.value(Prefix.NANO)
        delta_lambda_fwhm = p.delta_lambda_fwhm.value(Prefix.NANO)
        theta1_ps = p.theta1.value(Prefix.PICO)
        theta2_ps2 = p.theta2.value(Prefix.PICO)

        def curve(theta0_rad: float) -> np.ndarray:
            return spectrum_fit_skew(wl, p.R, p.L, theta0_rad, theta1_ps, theta2_ps2,
                                     p.alpha_R, p.epsilon_R, p.s_R,
                                     p.alpha_L, p.epsilon_L, p.s_L,
                                     p.offset, lambda0, delta_lambda_fwhm)

        set_phase_curve = curve(self._config.set_phase.Rad)
        current_phase_curve = curve(p.theta0.Rad)

        if self._plot_frequency:
            # Ω(λ) = 2π·c/λ − 2π·c/λ0, same mapping as spectrum_fit (Base_Core/base_core/math/functions.py:45-49)
            omega = 2.0 * np.pi * SPEED_OF_LIGHT / wl * 1e-3
            omega0 = 2.0 * np.pi * SPEED_OF_LIGHT / lambda0 * 1e-3
            x = omega - omega0
        else:
            x = wl

        self._set_phase_series.setData(x, set_phase_curve)
        self._current_phase_series.setData(x, current_phase_curve)

        if rescale and self._plot_item is not None:
            x_lo, x_hi = float(x.min()), float(x.max())
            x_pad = (x_hi - x_lo) * 0.1
            y_lo = float(min(set_phase_curve.min(), current_phase_curve.min()))
            y_hi = float(max(set_phase_curve.max(), current_phase_curve.max()))
            y_pad = (y_hi - y_lo) * 0.1 or 1.0
            self._plot_item.setXRange(x_lo - x_pad, x_hi + x_pad, padding=0)
            self._plot_item.setYRange(y_lo - y_pad, y_hi + y_pad, padding=0)

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

    def apply(self, set_phase_deg: float, fit_all_params: bool) -> None:
        """Commit pending values into the shared config and send to the subprocess."""
        self._config.set_phase = Angle(set_phase_deg, AngleUnit.DEG)
        self._config.fit_all_params = fit_all_params
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
