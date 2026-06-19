from __future__ import annotations

import numpy as np
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QWidget

from base_qt.ui.panel import Panel
from app_apps.analysis.phase_control.ui.phase_control_vm import PhaseControlVM


class PhaseControlPanel(Panel):
    def __init__(self, vm: PhaseControlVM, parent: QWidget | None = None) -> None:
        super().__init__("Phase Control", vm, parent)

    def setup(self) -> None:
        self._series = QLineSeries()

        self._x_axis = QValueAxis()
        self._x_axis.setTitleText("Wavelength (nm)")
        self._x_axis.setLabelFormat("%.0f")

        self._y_axis = QValueAxis()
        self._y_axis.setTitleText("Intensity")
        self._y_axis.setRange(0, 1)

        chart = QChart()
        chart.addSeries(self._series)
        chart.addAxis(self._x_axis, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(self._y_axis, Qt.AlignmentFlag.AlignLeft)
        self._series.attachAxis(self._x_axis)
        self._series.attachAxis(self._y_axis)
        chart.legend().hide()

        view = QChartView(chart)
        view.setMinimumHeight(220)
        self.body_layout.addWidget(view)

        self._connect(self.vm.spectrum_updated, self._on_spectrum_updated)

    def _on_spectrum_updated(self, wavelengths: np.ndarray, intensities: np.ndarray) -> None:
        points = [QPointF(float(w), float(i)) for w, i in zip(wavelengths, intensities)]
        self._series.replace(points)
        if len(wavelengths):
            self._x_axis.setRange(float(wavelengths[0]), float(wavelengths[-1]))
        if len(intensities):
            lo, hi = float(intensities.min()), float(intensities.max())
            pad = (hi - lo) * 0.05 or 1.0
            self._y_axis.setRange(lo - pad, hi + pad)
