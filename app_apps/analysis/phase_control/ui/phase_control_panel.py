from __future__ import annotations

import numpy as np
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QSizePolicy, QStackedWidget, QWidget

from base_qt.ui.panel import Panel
from app_apps.analysis.phase_control.subprocess.domain.mode import ControlMode
from app_apps.analysis.phase_control.ui.envelope_control_view import EnvelopeControlView
from app_apps.analysis.phase_control.ui.phase_config_view import PhaseConfigView
from app_apps.analysis.phase_control.ui.phase_control_view_model import PhaseControlViewModel
from app_apps.analysis.phase_control.ui.stabilization_control_view import StabilizationControlView


class PhaseControlPanel(Panel):
    def __init__(self, vm: PhaseControlViewModel, parent: QWidget | None = None) -> None:
        super().__init__("Phase Control", vm, parent)

    def setup(self) -> None:
        # --- Chart ---
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

        self.vm.stabilization_vm.set_chart(chart)
        self.vm.envelope_vm.set_chart(chart)
        self.vm.stabilization_vm.set_active(True)

        view = QChartView(chart)
        view.setMinimumHeight(220)

        # Config view — parented to this panel so it floats within it
        config_dialog = PhaseConfigView(self.vm.svc, self.vm.stabilization_vm, parent=self)

        # --- Controls row ---
        controls = QWidget()
        # Only take as much vertical space as content needs
        controls.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(0)

        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Phase Stabilization", ControlMode.PHASE_TRACKING)
        self._mode_combo.addItem("Envelope", ControlMode.ENVELOPE)
        row.addWidget(self._mode_combo)

        row.addSpacing(16)  # gap between mode selector and per-mode controls

        self._stacked = QStackedWidget()
        self._stacked.addWidget(StabilizationControlView(self.vm.stabilization_vm, config_dialog))
        self._stacked.addWidget(EnvelopeControlView(self.vm.envelope_vm))
        row.addWidget(self._stacked)

        # chart takes all remaining vertical space; controls bar stays compact
        self.body_layout.addWidget(controls)
        self.body_layout.addWidget(view, stretch=1)

        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._connect(self.vm.spectrum_updated, self._on_spectrum_updated)

    def _on_mode_changed(self, index: int) -> None:
        self._stacked.setCurrentIndex(index)
        mode = self._mode_combo.itemData(index)
        self.vm.stabilization_vm.set_active(mode == ControlMode.PHASE_TRACKING)
        self.vm.set_mode(mode)

    def _on_spectrum_updated(self, wavelengths: np.ndarray, intensities: np.ndarray) -> None:
        points = [QPointF(float(w), float(i)) for w, i in zip(wavelengths, intensities)]
        self._series.replace(points)
        if len(wavelengths):
            self._x_axis.setRange(float(wavelengths[0]), float(wavelengths[-1]))
        if len(intensities):
            lo, hi = float(intensities.min()), float(intensities.max())
            pad = (hi - lo) * 0.05 or 1.0
            self._y_axis.setRange(lo - pad, hi + pad)
