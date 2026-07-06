from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCharts import QLineSeries
from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPen

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_core.math.enums import AngleUnit
from base_core.math.functions import spectrum_fit
from base_core.math.models import Angle
from base_core.quantities.enums import Prefix
from base_qt.app.dispatcher import QtDispatcher
from app_apps.analysis.phase_control.events import PhaseTrackingStateChanged, StabilizationConfigChanged

if TYPE_CHECKING:
    from PySide6.QtCharts import QChart
    from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
    from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig


class StabilizationControlViewModel(QObject):
    worker_state_changed = Signal(object)  # WorkerStatus
    config_updated = Signal()              # subprocess synced new fit params

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
        self._chart: QChart | None = None
        self._set_phase_series: QLineSeries | None = None
        self._current_phase_series: QLineSeries | None = None
        self._active = False
        self._unsub = bus.subscribe(PhaseTrackingStateChanged, self._on_state_changed)
        self._unsub_cfg = bus.subscribe(StabilizationConfigChanged, self._on_config_updated)

    def set_chart(self, chart: QChart) -> None:
        self._chart = chart

        self._set_phase_series = QLineSeries()
        self._set_phase_series.setName("Set phase")
        set_phase_pen = QPen(QColor("red"))
        set_phase_pen.setStyle(Qt.PenStyle.DashLine)
        self._set_phase_series.setPen(set_phase_pen)

        self._current_phase_series = QLineSeries()
        self._current_phase_series.setName("Current phase")
        current_phase_pen = QPen(QColor("green"))
        current_phase_pen.setStyle(Qt.PenStyle.DashDotLine)
        self._current_phase_series.setPen(current_phase_pen)

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

    def _attach_curves(self) -> None:
        if self._chart is None or self._set_phase_series is None or self._current_phase_series is None:
            return
        for series in (self._set_phase_series, self._current_phase_series):
            self._chart.addSeries(series)
            for axis in self._chart.axes(Qt.Orientation.Horizontal):
                series.attachAxis(axis)
            for axis in self._chart.axes(Qt.Orientation.Vertical):
                series.attachAxis(axis)

    def _detach_curves(self) -> None:
        if self._chart is None:
            return
        for series in (self._set_phase_series, self._current_phase_series):
            if series is not None:
                self._chart.removeSeries(series)

    def _update_curves(self) -> None:
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

        def curve(theta0_rad: float) -> np.ndarray:
            return spectrum_fit(wl, p.A, theta0_rad, p.theta1, p.theta2, p.V, p.offset, lambda0, delta_lambda_fwhm)

        set_phase_curve = curve(self._config.set_phase.Rad)
        current_phase_curve = curve(p.theta0.Rad)

        self._ensure_axis_range(Qt.Orientation.Horizontal, float(wl[0]), float(wl[-1]))
        self._ensure_axis_range(
            Qt.Orientation.Vertical,
            float(min(set_phase_curve.min(), current_phase_curve.min())),
            float(max(set_phase_curve.max(), current_phase_curve.max())),
        )

        self._set_phase_series.replace([QPointF(float(w), float(i)) for w, i in zip(wl, set_phase_curve)])
        self._current_phase_series.replace([QPointF(float(w), float(i)) for w, i in zip(wl, current_phase_curve)])

    def _ensure_axis_range(self, orientation: Qt.Orientation, lo: float, hi: float) -> None:
        """Expand an axis to cover [lo, hi] only if it doesn't already overlap it.

        Leaves a range already driven by the live spectrum trace untouched, but
        rescues the chart's default (0, 1) QValueAxis range for the case where no
        spectrum has been received yet, so the fit curves aren't plotted off-screen.
        """
        if self._chart is None:
            return
        for axis in self._chart.axes(orientation):
            if axis.max() < lo or axis.min() > hi:
                axis.setRange(lo, hi)

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

    def reset(self) -> None:
        self._handle.reset()

    def apply(self, set_phase_deg: float, fit_all_params: bool) -> None:
        """Commit pending values into the shared config and send to the subprocess."""
        self._config.set_phase = Angle(set_phase_deg, AngleUnit.DEG)
        self._config.fit_all_params = fit_all_params
        self._handle.set_config(self._config)

    def _on_state_changed(self, _: PhaseTrackingStateChanged) -> None:
        state = self._handle.state
        self._dispatcher.post(lambda: self.worker_state_changed.emit(state))

    def _on_config_updated(self, _: StabilizationConfigChanged) -> None:
        def _apply() -> None:
            self._update_curves()
            self.config_updated.emit()

        self._dispatcher.post(_apply)
