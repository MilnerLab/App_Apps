from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

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
        "probe_only":          BoolSpec("Probe only (never command delay or grating)"),
        "record_spectra":      BoolSpec("Record spectra (joins a running spectrometer; never opens one)"),
    }

    _groups = [
        ("Probe (axis 1 — scanned)", ["probe_start_mm", "probe_stop_mm", "probe_step_mm", "probe_intercept_mm"]),
        ("Grating (axis 3 — chirp)", ["grating_start_mm", "grating_stop_mm", "grating_step_mm"]),
        ("Delay (axis 2 — central frequency)",
         ["delay_base_start_mm", "delay_base_stop_mm", "delay_base_step_mm", "delay_slope", "delay_intercept_mm"]),
        ("Adaptive step", ["adaptive_probe_step", "probe_oversample", "probe_step_max_mm"]),
        ("Acquisition", ["n_traces", "settle_s", "timeout_s", "channel", "mock_scope", "probe_only", "record_spectra"]),
    ]

    # Operator scan designs (grating start:step:stop, delay base start:step:stop) in the
    #: Everything the two scan designs agree on. Held apart from the presets themselves
    #: so a probe/acquisition change is made once rather than being edited in both and
    #: silently diverging.
    _COMMON: dict[str, object] = {
        "probe_start_mm": 0.0,
        "probe_stop_mm": 120.0,
        "probe_intercept_mm": 96.0,
        "adaptive_probe_step": True,
        "probe_oversample": 4.0,
        "probe_step_mm": 0.15,
        "probe_step_max_mm": 1.0,
        "delay_slope": -0.004857142857142858,
        "delay_intercept_mm": 18.585714285714285,
        "n_traces": 5,
        "record_spectra": True,
        # Explicitly off: a preset is a full scan design over both axes, so loading one
        # must disarm a probe-only pin rather than silently inherit it.
        "probe_only": False,
    }

    #: The two operator scan designs. ``step_mode`` is part of the design, not a separate
    #: choice: scan_L walks the grating and wants a look at each position, while scan_d
    #: sits at one grating position and sweeps delay, where there is nothing to align
    #: between points.
    _presets: dict[str, dict[str, object]] = {
        "scan_L  (grating 15→−75)": {
            **_COMMON,
            "run_name": "scan_L",
            "step_mode": True,
            "grating_start_mm": 15.0,
            "grating_step_mm": 10.0,
            "grating_stop_mm": -75.0,
            "delay_base_start_mm": 0.0,
            "delay_base_stop_mm": 0.0,
            "delay_base_step_mm": 1.0,
        },
        "scan_d  (delay 0.1→1.5)": {
            **_COMMON,
            "run_name": "scan_d",
            "step_mode": False,
            "grating_start_mm": 30.0,
            "grating_step_mm": 1.0,
            "grating_stop_mm": 30.0,
            "delay_base_start_mm": 0.1,
            "delay_base_stop_mm": 1.5,
            "delay_base_step_mm": 0.2,
        },
    }

    def __init__(self, vm: XcorrViewModel, parent: QWidget) -> None:
        self._vm = vm
        super().__init__("XCORR Scan", vm.settings, parent, vm=vm)

        # Where the run lands, and what it is called. Both are free text rather than
        # spin boxes, so they sit outside the generated field grid.
        out_row = QHBoxLayout()
        out_label = QLabel("Output folder")
        out_label.setMinimumWidth(130)
        self._out_dir_edit = QLineEdit(str(vm.settings.out_dir))
        self._out_dir_edit.setPlaceholderText("folder for the XCORR_*.h5 run files")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._on_browse)
        out_row.addWidget(out_label)
        out_row.addWidget(self._out_dir_edit, stretch=1)
        out_row.addWidget(browse_btn)
        self.body_layout.addLayout(out_row)

        name_row = QHBoxLayout()
        name_label = QLabel("Run name")
        name_label.setMinimumWidth(130)
        self._run_name_edit = QLineEdit(vm.settings.run_name)
        self._run_name_edit.setPlaceholderText("tag for the filename, e.g. scan_L_1")
        name_row.addWidget(name_label)
        name_row.addWidget(self._run_name_edit, stretch=1)
        self.body_layout.addLayout(name_row)

        # Probe-only arming. Pins the grating and delay ranges to the live stage
        # positions and stops the routine commanding either axis -- for runs where those
        # two are aligned by hand and moving them would destroy the alignment.
        pin_row = QHBoxLayout()
        self._pin_btn = QPushButton("Pin stages here (probe only)")
        self._pin_btn.setToolTip(
            "Read the delay and grating positions, pin the scan to them, and sweep the "
            "probe alone. Neither stage is commanded, not even to where it already is."
        )
        self._pin_btn.clicked.connect(self._on_pin)
        pin_row.addWidget(self._pin_btn)
        pin_row.addStretch(1)
        self.body_layout.addLayout(pin_row)

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
        # Step mode is armed by default: the operator almost always wants to look at the
        # first setpoint before committing the rest of the grid to it.
        self._step_mode_box = QCheckBox("Step mode (hold at each grating position)")
        self._step_mode_box.setChecked(True)
        self._step_mode_box.toggled.connect(self._on_step_mode)
        self._step_btn = QPushButton("Step")
        self._step_btn.clicked.connect(vm.step)
        self._step_btn.setEnabled(False)
        self._abort_btn = QPushButton("Abort")
        self._abort_btn.clicked.connect(vm.abort)
        self._abort_btn.setEnabled(False)
        controls.addWidget(self._start_btn)
        controls.addWidget(self._pause_btn)
        controls.addWidget(self._step_mode_box)
        controls.addWidget(self._step_btn)
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
        vm.bind_reload(self._populate)

    def _apply_preset(self, preset_name: str, values: dict[str, object]) -> None:
        # Flush the current widget edits into the settings first so a preset only
        # overrides the fields it names and preserves everything else the user typed.
        self._apply()
        step_mode = values.get("step_mode")
        for name, val in values.items():
            # Not a settings field -- it drives the checkbox, handled below.
            if name == "step_mode":
                continue
            setattr(self._vm.settings, name, val)
        self._populate()
        if step_mode is not None:
            # Both, and in this order: the box is what the operator reads, the view model
            # is what the next Start applies.
            self._step_mode_box.setChecked(bool(step_mode))
            self._vm.set_step_mode(bool(step_mode))
        mode = ("" if step_mode is None
                else " · step mode ON (hold at each grating position)" if step_mode
                else " · step mode OFF (free-running)")
        # Visible confirmation — the spin boxes update in place, which is easy to miss,
        # so also say so on the status line.
        self._status.setText(
            f"Loaded {preset_name} — review the ranges, then Start scan{mode}")

    def _on_pin(self) -> None:
        # Flush first, for the same reason the presets do: pinning overwrites only the
        # grating and delay ranges, and everything else the operator typed must survive.
        self._apply()
        self._vm.pin_stages_here()

    def _on_browse(self) -> None:
        start = self._out_dir_edit.text().strip() or str(Path.cwd())
        chosen = QFileDialog.getExistingDirectory(self, "XCORR output folder", start)
        if chosen:
            self._out_dir_edit.setText(str(Path(chosen)))

    def _populate(self) -> None:
        super()._populate()
        # getattr guards: the base class populates during __init__, before these exist.
        if getattr(self, "_out_dir_edit", None) is not None:
            self._out_dir_edit.setText(str(self._config.out_dir))
        if getattr(self, "_run_name_edit", None) is not None:
            self._run_name_edit.setText(self._config.run_name)

    def _apply(self) -> None:
        super()._apply()
        if getattr(self, "_out_dir_edit", None) is not None:
            text = self._out_dir_edit.text().strip()
            # A blank box means "leave it alone" rather than "write to the root": echo the
            # value actually in force back into the widget so the two cannot disagree.
            if text:
                self._config.out_dir = Path(text)
            self._out_dir_edit.setText(str(self._config.out_dir))
        if getattr(self, "_run_name_edit", None) is not None:
            self._config.run_name = self._run_name_edit.text().strip()

    def _on_step_mode(self, enabled: bool) -> None:
        self._vm.set_step_mode(enabled)
        # Step only means anything while a run is up and stepping is armed.
        self._step_btn.setEnabled(enabled and self._abort_btn.isEnabled())

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
        self._pin_btn.setEnabled(not running)
        self._abort_btn.setEnabled(running)
        self._pause_btn.setEnabled(running)
        self._step_btn.setEnabled(running and self._step_mode_box.isChecked())
        if not running:
            # Run ended — clear the toggle so the next scan starts on "Pause".
            self._paused = False
            self._pause_btn.setText("Pause")

    def on_apply(self) -> None:
        pass
