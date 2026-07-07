from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QSizePolicy, QStackedWidget, QWidget

from app_apps.io.spectrometer.domain.helpers import normalize_spectrum
from base_core.quantities.constants import SPEED_OF_LIGHT
from base_core.quantities.enums import Prefix
from base_qt.ui.panel import Panel
from app_apps.analysis.phase_control.subprocess.domain.mode import ControlMode
from app_apps.analysis.phase_control.ui.envelope_control_view import EnvelopeControlView
from app_apps.analysis.phase_control.ui.phase_config_view import PhaseConfigView
from app_apps.analysis.phase_control.ui.phase_control_view_model import PhaseControlViewModel
from app_apps.analysis.phase_control.ui.stabilization_control_view import StabilizationControlView


class PhaseControlView(Panel):
    def __init__(self, vm: PhaseControlViewModel, parent: QWidget | None = None) -> None:
        super().__init__("Phase Control", vm, parent)

    def setup(self) -> None:
        # --- Plot ---
        self._plot = pg.PlotWidget()
        self._plot.showGrid(x=True, y=True, alpha=0.3)
        self._plot.setLabel("bottom", "Wavelength (nm)")
        self._plot.setLabel("left", "Intensity")
        self._plot.setMinimumHeight(220)
        self._live_curve = self._plot.plot()

        plot_item = self._plot.getPlotItem()
        self.vm.stabilization_vm.set_chart(plot_item)
        self.vm.envelope_vm.set_chart(plot_item)
        self.vm.stabilization_vm.set_active(True)

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
        self.body_layout.addWidget(self._plot, stretch=1)

        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._connect(self.vm.spectrum_updated, self._on_spectrum_updated)
        self._connect(self.vm.stabilization_vm.plot_mode_changed, self._update_axis_label)

    def _on_mode_changed(self, index: int) -> None:
        self._stacked.setCurrentIndex(index)
        mode = self._mode_combo.itemData(index)
        self.vm.stabilization_vm.set_active(mode == ControlMode.PHASE_TRACKING)
        self.vm.set_mode(mode)
        self._update_axis_label()

    def _update_axis_label(self, *_: object) -> None:
        freq = self.vm.svc.mode == ControlMode.PHASE_TRACKING and self.vm.stabilization_vm.plot_frequency
        self._plot.setLabel("bottom", "Detuning Ω (rad/ps)" if freq else "Wavelength (nm)")

    def _on_spectrum_updated(self, wavelengths: np.ndarray, intensities: np.ndarray) -> None:
        x = wavelengths
        if self.vm.svc.mode == ControlMode.PHASE_TRACKING:
            cfg = self.vm.stabilization_vm.config
            wl_min = cfg.wavelength_range.min.value(Prefix.NANO)
            wl_max = cfg.wavelength_range.max.value(Prefix.NANO)
            x, intensities = normalize_spectrum(x, intensities, wl_min, wl_max)
            if self.vm.stabilization_vm.plot_frequency:
                lambda0_nm = cfg.params.lambda0.value(Prefix.NANO)
                # Ω(λ) = 2π·c/λ − 2π·c/λ0, same mapping as spectrum_fit (Base_Core/base_core/math/functions.py:45-49)
                omega = 2.0 * np.pi * SPEED_OF_LIGHT / x * 1e-3
                omega0 = 2.0 * np.pi * SPEED_OF_LIGHT / lambda0_nm * 1e-3
                x = omega - omega0
        self._live_curve.setData(x, intensities)
