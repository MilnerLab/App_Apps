from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from base_qt.ui.form import BoolSpec, ConfigForm, FloatSpec, IntSpec

from app_apps.routines.xcorr.ui.view_model import XcorrViewModel


def _mm(label: str, lo: float, hi: float, step: float = 0.1) -> FloatSpec:
    return FloatSpec(label, min=lo, max=hi, decimals=4, step=step, suffix="mm")


class XcorrView(ConfigForm):
    """Routines → XCORR Scan.

    An auto-generated editor over the mutable :class:`XcorrSettings`, plus Start /
    Abort and a live status line fed by the view-model's marshalled routine events.
    Bounds here are generous guard-rails on data entry only — the planner is the
    real authority and refuses any out-of-limit *commanded* setpoint before motion.
    """

    _specs = {
        "probe_start_mm":      _mm("Probe start", -300.0, 300.0),
        "probe_stop_mm":       _mm("Probe stop", -300.0, 300.0),
        "probe_step_mm":       _mm("Probe step (floor)", 1e-4, 100.0, step=0.01),
        "probe_intercept_mm":  _mm("Probe intercept @ g=0", -300.0, 300.0),

        "grating_start_mm":    _mm("Grating start", -75.0, 75.0),
        "grating_stop_mm":     _mm("Grating stop", -75.0, 75.0),
        "grating_step_mm":     _mm("Grating step", 1e-4, 150.0),

        "delay_base_start_mm": _mm("Delay base start", -50.0, 50.0),
        "delay_base_stop_mm":  _mm("Delay base stop", -50.0, 50.0),
        "delay_base_step_mm":  _mm("Delay base step", 1e-4, 100.0),
        "delay_slope":         FloatSpec("Delay slope (mm/mm)", min=-1.0, max=1.0, decimals=5, step=0.001),
        "delay_intercept_mm":  _mm("Delay intercept", -50.0, 50.0),

        "adaptive_probe_step": BoolSpec("Adaptive probe step (Nyquist)"),
        "probe_oversample":    FloatSpec("Oversample", min=0.1, max=100.0, decimals=2, step=0.5),
        "probe_step_max_mm":   _mm("Probe step (coarsest)", 1e-3, 100.0, step=0.1),

        "n_traces":            IntSpec("Traces / point", min=1, max=100_000),
        "settle_s":            FloatSpec("Settle", min=0.0, max=3600.0, decimals=3, step=0.1, suffix="s"),
        "timeout_s":           FloatSpec("Move/acq timeout", min=1.0, max=3600.0, decimals=1, step=1.0, suffix="s"),
        "channel":             IntSpec("Scope channel", min=1, max=2),
        "mock_scope":          BoolSpec("Mock scope (no laser / no TDS)"),
    }

    _groups = [
        ("Probe (axis 1 — scanned)", ["probe_start_mm", "probe_stop_mm", "probe_step_mm", "probe_intercept_mm"]),
        ("Grating (axis 3 — chirp)", ["grating_start_mm", "grating_stop_mm", "grating_step_mm"]),
        ("Delay (axis 2 — central frequency)",
         ["delay_base_start_mm", "delay_base_stop_mm", "delay_base_step_mm", "delay_slope", "delay_intercept_mm"]),
        ("Adaptive step", ["adaptive_probe_step", "probe_oversample", "probe_step_max_mm"]),
        ("Acquisition", ["n_traces", "settle_s", "timeout_s", "channel", "mock_scope"]),
    ]

    # Operator scan designs (grating start:step:stop, delay base start:step:stop) in the
    # MATLAB colon convention. Delay tracks the grating one-to-one via the standard
    # correction f(grating) = -0.005·grating + 17.31 (zero-frequency line, XCORR scan
    # design 2026-07-20), so each preset also sets delay_slope / delay_intercept. Fields
    # not listed (probe range, acquisition) are left as the operator has them.
    _presets: dict[str, dict[str, float]] = {
        # grating = -75:5:30, delay = f(grating) + 0:0.5:1
        "Preset 1  (grating -75:5:30)": {
            "grating_start_mm": -75.0, "grating_step_mm": 5.0, "grating_stop_mm": 30.0,
            "delay_base_start_mm": 0.0, "delay_base_step_mm": 0.5, "delay_base_stop_mm": 1.0,
            "delay_slope": -0.005, "delay_intercept_mm": 17.31,
        },
        # grating = -75:105.1/2:30.1, delay = f(grating) + 0:0.15:1.5
        "Preset 2  (grating -75:52.55:30.1)": {
            "grating_start_mm": -75.0, "grating_step_mm": 105.1 / 2.0, "grating_stop_mm": 30.1,
            "delay_base_start_mm": 0.0, "delay_base_step_mm": 0.15, "delay_base_stop_mm": 1.5,
            "delay_slope": -0.005, "delay_intercept_mm": 17.31,
        },
    }

    def __init__(self, vm: XcorrViewModel, parent: QWidget) -> None:
        self._vm = vm
        super().__init__("XCORR Scan", vm.settings, parent, vm=vm)

        # One button per operator scan design — loads its grating/delay values into the
        # form (existing widget edits are flushed first, so nothing you typed is lost).
        presets = QHBoxLayout()
        presets.addWidget(QLabel("Presets:"))
        for name, values in self._presets.items():
            btn = QPushButton(name)
            btn.clicked.connect(lambda _=False, n=name, v=values: self._apply_preset(n, v))
            presets.addWidget(btn)
        presets.addStretch(1)
        self.body_layout.addLayout(presets)

        controls = QHBoxLayout()
        self._start_btn = QPushButton("Start scan")
        self._start_btn.clicked.connect(self._on_start)
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._on_pause)
        self._pause_btn.setEnabled(False)
        self._abort_btn = QPushButton("Abort")
        self._abort_btn.clicked.connect(vm.abort)
        self._abort_btn.setEnabled(False)
        controls.addWidget(self._start_btn)
        controls.addWidget(self._pause_btn)
        controls.addWidget(self._abort_btn)
        controls.addStretch(1)
        self.body_layout.addLayout(controls)

        self._status = QLabel("idle")
        self._status.setWordWrap(True)
        self.body_layout.addWidget(self._status)

        #: Local mirror of the pause toggle — the routine publishes no pause event, so
        #: the button drives its own label. Reset whenever a run ends (running=False).
        self._paused = False

        vm.bind_update(self._render)

    def _apply_preset(self, preset_name: str, values: dict[str, float]) -> None:
        # Flush the current widget edits into the settings first so a preset only
        # overrides the fields it names and preserves everything else the user typed.
        self._apply()
        for name, val in values.items():
            setattr(self._vm.settings, name, val)
        self._populate()
        # Visible confirmation — the spin boxes update in place, which is easy to miss,
        # so also say so on the status line.
        self._status.setText(f"Loaded {preset_name} — review the ranges, then Start scan")

    def _on_pause(self) -> None:
        if self._paused:
            self._vm.resume()
        else:
            self._vm.pause()
        self._paused = not self._paused
        self._pause_btn.setText("Resume" if self._paused else "Pause")

    def _on_start(self) -> None:
        # Flush the widgets into the bound settings before building the config.
        self._apply()
        self._vm.start()

    def _render(self, text: str, running: bool) -> None:
        self._status.setText(text)
        self._start_btn.setEnabled(not running)
        self._abort_btn.setEnabled(running)
        self._pause_btn.setEnabled(running)
        if not running:
            # Run ended — clear the toggle so the next scan starts on "Pause".
            self._paused = False
            self._pause_btn.setText("Pause")

    def on_apply(self) -> None:
        pass
