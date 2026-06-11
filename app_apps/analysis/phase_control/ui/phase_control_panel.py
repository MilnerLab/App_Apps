from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QSizePolicy, QWidget

from app_apps.analysis.phase_control.subprocess.domain.mode import ControlMode
from base_qt.ui.panel import Panel

from app_apps.analysis.phase_control.ui.phase_control_vm import PhaseControlVM

_MAX_HISTORY = 1000


class PhaseControlPanel(Panel):
    """
    Phase control panel.

    State machine for the two control buttons:

        STOPPED  →  [Start ✓]  [Pause  ✗]
        RUNNING  →  [Start ✗]  [Pause  ✓]
        PAUSED   →  [Start ✓]  [Reset  ✓]   (Pause button relabelled)

    Start (from PAUSED) resumes without restarting the subprocess.
    Reset stops the service and returns to STOPPED.
    """

    def __init__(self, vm: PhaseControlVM) -> None:
        super().__init__("Phase Control", vm)

    @property
    def vm(self) -> PhaseControlVM:
        return self.__dict__["vm"]  # type: ignore[return-value]

    def setup(self) -> None:
        self._t0: float | None = None
        self._xs: list[float] = []
        self._ys: list[float] = []

        # ── controls bar ───────────────────────────────────────────────
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self._mode_combo = QComboBox()
        for mode in ControlMode:
            self._mode_combo.addItem(mode.value.replace("_", " ").title(), userData=mode)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row.addWidget(self._mode_combo)

        row.addStretch(1)

        self._config_btn = QPushButton("Config...")
        self._config_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._config_btn.clicked.connect(self._on_config)
        row.addWidget(self._config_btn)

        self._start_btn = QPushButton("Start")
        self._start_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._start_btn.clicked.connect(self._on_start)
        row.addWidget(self._start_btn)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._pause_btn.clicked.connect(self._on_pause_reset)
        row.addWidget(self._pause_btn)

        self.body_layout.addWidget(bar)

        # ── correction plot ─────────────────────────────────────────────
        self._plot = pg.PlotWidget()
        self._plot.setBackground(None)
        self._plot.setLabel("left", "Correction", units="°")
        self._plot.setLabel("bottom", "Time", units="s")
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._curve = self._plot.plot(pen=pg.mkPen("#4ea6ff", width=1.5))
        self.body_layout.addWidget(self._plot, stretch=1)

        # ── spectrum plot ────────────────────────────────────────────────
        self._spec_plot = pg.PlotWidget()
        self._spec_plot.setBackground(None)
        self._spec_plot.setLabel("left", "Intensity", units="counts")
        self._spec_plot.setLabel("bottom", "Wavelength", units="nm")
        self._spec_plot.showGrid(x=True, y=True, alpha=0.15)
        self._spec_curve = self._spec_plot.plot(pen=pg.mkPen("#ff9f4a", width=1.5))
        self.body_layout.addWidget(self._spec_plot, stretch=1)

        # ── signal connections ──────────────────────────────────────────
        self._connect(self.vm.correction_updated, self._on_correction)
        self._connect(self.vm.running_changed,    self._on_running_changed)
        self._connect(self.vm.paused_changed,     self._on_paused_changed)
        self._connect(self.vm.spectrum_updated,   self._on_spectrum)

        # Apply initial state
        if self.vm.is_running:
            self._enter_running()
        else:
            self._enter_stopped()

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def _enter_stopped(self) -> None:
        self._start_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("Pause")

    def _enter_running(self) -> None:
        self._start_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._pause_btn.setText("Pause")

    def _enter_paused(self) -> None:
        self._start_btn.setEnabled(True)
        self._pause_btn.setEnabled(True)
        self._pause_btn.setText("Reset")

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_running_changed(self, running: bool) -> None:
        if running:
            self._enter_running()
        else:
            self._enter_stopped()

    def _on_paused_changed(self, paused: bool) -> None:
        if paused:
            self._enter_paused()
        elif self.vm.is_running:
            self._enter_running()
        # else: service stopped — _on_running_changed(False) handles it

    def _on_correction(self, correction_deg: float) -> None:
        if self._t0 is None:
            self._t0 = time.monotonic()
        t = time.monotonic() - self._t0
        self._xs.append(t)
        self._ys.append(correction_deg)
        if len(self._xs) > _MAX_HISTORY:
            self._xs = self._xs[-_MAX_HISTORY:]
            self._ys = self._ys[-_MAX_HISTORY:]
        self._curve.setData(self._xs, self._ys)

    def _on_spectrum(self, wavelengths: object, intensities: object) -> None:
        self._spec_curve.setData(wavelengths, intensities)

    def _on_mode_changed(self, _: int) -> None:
        mode: ControlMode = self._mode_combo.currentData()
        self.vm.set_mode(mode)

    # ------------------------------------------------------------------
    # Button click handlers
    # ------------------------------------------------------------------

    def _on_config(self) -> None:
        self.vm.open_config(self)

    def _on_start(self) -> None:
        self.vm.start_worker()

    def _on_pause_reset(self) -> None:
        if self._pause_btn.text() == "Pause":
            self.vm.pause()
        else:
            self.vm.reset()
