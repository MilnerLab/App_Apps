from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QSizePolicy, QWidget

from base_qt.ui.panel import Panel

from app_apps.analysis.phase_control.domain.mode import ControlMode
from app_apps.analysis.phase_control.ui.phase_control_vm import PhaseControlVM

_MAX_HISTORY = 1000


class PhaseControlPanel(Panel):
    """
    Phase control panel.

    Layout:
        [Mode ▼]  [Start/Stop]  [Pause/Resume]  [Reset]
        ─────────────────────────────────────────────────
        rolling correction-angle plot (pyqtgraph)
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

        running = self.vm.is_running

        self._start_btn = QPushButton("Stop" if running else "Start")
        self._start_btn.setCheckable(True)
        self._start_btn.setChecked(running)
        self._start_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._start_btn.clicked.connect(self._on_start_stop)
        row.addWidget(self._start_btn)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setCheckable(True)
        self._pause_btn.setEnabled(running)
        self._pause_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._pause_btn.clicked.connect(self.vm.toggle_pause)
        row.addWidget(self._pause_btn)

        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setEnabled(running)
        self._reset_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._reset_btn.clicked.connect(self.vm.reset)
        row.addWidget(self._reset_btn)

        self.body_layout.addWidget(bar)

        # ── plot ────────────────────────────────────────────────────────
        self._plot = pg.PlotWidget()
        self._plot.setBackground(None)
        self._plot.setLabel("left", "Correction", units="°")
        self._plot.setLabel("bottom", "Time", units="s")
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._curve = self._plot.plot(pen=pg.mkPen("#4ea6ff", width=1.5))
        self.body_layout.addWidget(self._plot, stretch=1)

        # ── signal connections ──────────────────────────────────────────
        self._connect(self.vm.correction_updated, self._on_correction)
        self._connect(self.vm.running_changed,    self._on_running_changed)
        self._connect(self.vm.paused_changed,     self._on_paused_changed)

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------

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

    def _on_running_changed(self, running: bool) -> None:
        self._start_btn.setChecked(running)
        self._start_btn.setText("Stop" if running else "Start")
        self._pause_btn.setEnabled(running)
        self._reset_btn.setEnabled(running)
        if not running:
            self._pause_btn.setChecked(False)
            self._pause_btn.setText("Pause")

    def _on_paused_changed(self, paused: bool) -> None:
        self._pause_btn.setChecked(paused)
        self._pause_btn.setText("Resume" if paused else "Pause")

    def _on_mode_changed(self, _index: int) -> None:
        mode: ControlMode = self._mode_combo.currentData()
        self.vm.set_mode(mode)

    def _on_start_stop(self) -> None:
        if self._start_btn.isChecked():
            self.vm.start()
        else:
            self.vm.stop()
