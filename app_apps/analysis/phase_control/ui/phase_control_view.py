from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QWidget,
)

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

        # The running mean of the raw frames in the current averaging block: the trace the
        # correction is computed from, as opposed to the single frame the eye happens to
        # catch. Drawn here rather than in the view model because, like the live curve, it is
        # built from the raw spectrum and shares its x mapping.
        avg_pen = QPen(QColor("#ffb000"))
        avg_pen.setCosmetic(True)
        self._avg_curve = self._plot.plot(pen=avg_pen)

        # The first frame collected after a correction, kept on screen until the next one
        # replaces it. Thick and purple because it is the one frame that answers "did the
        # move do what it was supposed to": everything before it describes the old plate
        # position. It is also, at the instant it is drawn, exactly the running average --
        # the block has one frame in it -- so it stays visible for either toggle.
        post_pen = QPen(QColor("#a24bff"))
        post_pen.setWidthF(2.5)
        post_pen.setCosmetic(True)
        self._post_curve = self._plot.plot(pen=post_pen)

        # Running-average accumulator. Summed rather than averaged incrementally so a
        # resize of the block, or a frame arriving on a different grid, is a reset and not a
        # silently wrong mean.
        self._acc_x: np.ndarray | None = None
        self._acc_sum: np.ndarray | None = None
        self._acc_n = 0
        # Armed by a correction, fired by the first frame after the plate has settled. The
        # settle time is the loop's own move_settle_s, so this marks the same frame the loop
        # treats as the first of the new block. None = not armed.
        self._post_at: float | None = None

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

        self._save_csv_btn = QPushButton("Save CSV")
        self._save_csv_btn.setToolTip("Save the current spectrum to a CSV file")
        row.addWidget(self._save_csv_btn)

        # chart takes all remaining vertical space; controls bar stays compact
        self.body_layout.addWidget(controls)
        self.body_layout.addWidget(self._plot, stretch=1)

        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._save_csv_btn.clicked.connect(self._on_save_csv)
        self._connect(self.vm.spectrum_updated, self._on_spectrum_updated)
        self._connect(self.vm.stabilization_vm.plot_mode_changed, self._update_axis_label)
        # The Raw toggle is offered on the stabilization panel but the live curve is owned
        # here, so the visibility travels out rather than being applied where it is set.
        self._connect(self.vm.stabilization_vm.raw_visible_changed,
                      self._live_curve.setVisible)
        self._connect(self.vm.stabilization_vm.avg_visible_changed, self._on_avg_visible)
        self._connect(self.vm.stabilization_vm.block_reset, self._reset_average)
        self._connect(self.vm.stabilization_vm.correction_issued, self._on_correction)
        self._live_curve.setVisible(self.vm.stabilization_vm.show_raw)
        self._on_avg_visible(self.vm.stabilization_vm.show_avg)

    # ------------------------------------------------------------- block-average trace --
    def _on_avg_visible(self, visible: bool) -> None:
        if not visible:
            self._avg_curve.clear()
        elif self._acc_n:
            self._avg_curve.setData(self._acc_x, self._acc_sum / self._acc_n)
        self._update_post_visible()

    def _update_post_visible(self) -> None:
        """The post-correction frame belongs to Raw and to Avg alike.

        At the moment it is drawn the block holds exactly one frame, so it IS the running
        average as well as a raw trace. Hiding it with only one of the two switches would
        remove a curve the other switch says should be there.
        """
        vm = self.vm.stabilization_vm
        self._post_curve.setVisible(vm.show_raw or vm.show_avg)

    def _reset_average(self) -> None:
        self._acc_x = None
        self._acc_sum = None
        self._acc_n = 0
        self._avg_curve.clear()

    def _on_correction(self, _deg: float) -> None:
        # Wait out the plate move before marking a frame: a spectrum taken mid-rotation
        # belongs to neither position, which is exactly why the loop discards those too.
        settle = float(self.vm.stabilization_vm.config.move_settle_s)
        self._post_at = time.perf_counter() + settle

    def _accumulate(self, x: np.ndarray, y: np.ndarray) -> None:
        if self._acc_sum is None or self._acc_x is None or self._acc_sum.shape != y.shape:
            self._acc_x = np.array(x, dtype=float)
            self._acc_sum = np.array(y, dtype=float)
            self._acc_n = 1
        else:
            self._acc_x = np.array(x, dtype=float)
            self._acc_sum += y
            self._acc_n += 1
        if self.vm.stabilization_vm.show_avg:
            self._avg_curve.setData(self._acc_x, self._acc_sum / self._acc_n)

        if self._post_at is not None and time.perf_counter() >= self._post_at:
            self._post_at = None
            self._post_curve.setData(np.array(x, dtype=float), np.array(y, dtype=float))
            self._update_post_visible()

    def _on_save_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Spectrum", "spectrum.csv", "CSV files (*.csv);;All files (*)"
        )
        if path:
            self.vm.save_spectrum_csv(path)

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
            # Window to the analysis band but keep RAW counts — the cubic-phase
            # overlay (mid+half·cos) is drawn in raw counts, so they share a scale.
            mask = (x >= wl_min) & (x <= wl_max)
            x, intensities = x[mask], intensities[mask]
            if self.vm.stabilization_vm.plot_frequency:
                lambda_ref_nm = cfg.params.lambda_ref.value(Prefix.NANO)
                # Ω(λ) = 2π·c/λ − 2π·c/λ_ref (detuning axis, referenced to λ_ref).
                omega = 2.0 * np.pi * SPEED_OF_LIGHT / x * 1e-3
                omega0 = 2.0 * np.pi * SPEED_OF_LIGHT / lambda_ref_nm * 1e-3
                x = omega - omega0
        self._live_curve.setData(x, intensities)
        # Only in phase tracking: the envelope mode has its own grid and no averaging block,
        # so frames from it would land in a mean that nothing is correcting on.
        if self.vm.svc.mode == ControlMode.PHASE_TRACKING:
            self._accumulate(x, intensities)
