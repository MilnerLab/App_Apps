from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app_apps.analysis.phase_control.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.service import PhaseControlService
from base_core.math.models import Angle, Range
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Length


def _nm_spin(min_nm: float = 700.0, max_nm: float = 1000.0) -> QDoubleSpinBox:
    sb = QDoubleSpinBox()
    sb.setRange(min_nm, max_nm)
    sb.setDecimals(3)
    sb.setSuffix(" nm")
    return sb


def _float_spin(lo: float, hi: float, decimals: int = 4, step: float = 0.01) -> QDoubleSpinBox:
    sb = QDoubleSpinBox()
    sb.setRange(lo, hi)
    sb.setDecimals(decimals)
    sb.setSingleStep(step)
    return sb


class PhaseConfigDialog(QDialog):
    """
    Modal dialog for editing StabilizationConfig.

    Opens from PhaseStatusPanel → "Configure…".
    Apply pushes the current values to the phase tracking worker.
    """

    def __init__(self, svc: PhaseControlService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Phase Tracking Configuration")
        self._svc = svc
        cfg = svc._config

        root = QVBoxLayout(self)

        # --- Spectral fit parameters ---
        fit_box = QGroupBox("Spectral Fit")
        fit_form = QFormLayout(fit_box)

        self._central_wl = _nm_spin()
        self._central_wl.setValue(float(cfg.central_wavelength.value(Prefix.NANO)))
        fit_form.addRow("Central wavelength", self._central_wl)

        self._bandwidth = _nm_spin(0.1, 50.0)
        self._bandwidth.setValue(float(cfg.bandwidth.value(Prefix.NANO)))
        fit_form.addRow("Bandwidth", self._bandwidth)

        self._baseline = _float_spin(-10.0, 10.0)
        self._baseline.setValue(cfg.baseline)
        fit_form.addRow("Baseline", self._baseline)

        self._tau_ps = _float_spin(0.0, 10.0, decimals=4)
        self._tau_ps.setValue(cfg.tau_ps)
        fit_form.addRow("τ (ps)", self._tau_ps)

        self._a_R = _float_spin(-10.0, 10.0)
        self._a_R.setValue(cfg.a_R_THz_per_ps)
        fit_form.addRow("a_R (THz/ps)", self._a_R)

        self._a_L = _float_spin(-10.0, 10.0)
        self._a_L.setValue(cfg.a_L_THz_per_ps)
        fit_form.addRow("a_L (THz/ps)", self._a_L)

        self._has_accel = QCheckBox()
        self._has_accel.setChecked(cfg.has_acceleration)
        fit_form.addRow("Asymmetric chirp", self._has_accel)

        root.addWidget(fit_box)

        # --- Tracking parameters ---
        track_box = QGroupBox("Tracking")
        track_form = QFormLayout(track_box)

        self._wl_min = _nm_spin()
        self._wl_min.setValue(float(cfg.wavelength_range.min.value(Prefix.NANO)))
        track_form.addRow("Wavelength range min", self._wl_min)

        self._wl_max = _nm_spin()
        self._wl_max.setValue(float(cfg.wavelength_range.max.value(Prefix.NANO)))
        track_form.addRow("Wavelength range max", self._wl_max)

        self._residuals_threshold = _float_spin(0.0, 1000.0, decimals=1, step=1.0)
        self._residuals_threshold.setValue(cfg.residuals_threshold)
        track_form.addRow("Residuals threshold", self._residuals_threshold)

        self._avg_spectra = QSpinBox()
        self._avg_spectra.setRange(1, 100)
        self._avg_spectra.setValue(cfg.avg_spectra)
        track_form.addRow("Averaging window", self._avg_spectra)

        root.addWidget(track_box)

        # --- Buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._apply)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _apply(self) -> None:
        cfg = self._svc._config
        cfg.central_wavelength    = Length(self._central_wl.value(), Prefix.NANO)
        cfg.bandwidth             = Length(self._bandwidth.value(), Prefix.NANO)
        cfg.baseline              = self._baseline.value()
        cfg.tau_ps                = self._tau_ps.value()
        cfg.a_R_THz_per_ps        = self._a_R.value()
        cfg.a_L_THz_per_ps        = self._a_L.value()
        cfg.has_acceleration      = self._has_accel.isChecked()
        cfg.wavelength_range      = Range(
            Length(self._wl_min.value(), Prefix.NANO),
            Length(self._wl_max.value(), Prefix.NANO),
        )
        cfg.residuals_threshold   = self._residuals_threshold.value()
        cfg.avg_spectra           = self._avg_spectra.value()
        self._svc.set_config()
