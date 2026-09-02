"""Mutable, form-editable scan settings for the XCORR UI.

:class:`XcorrConfig` is a *frozen* dataclass — the routine's run record must not
change under it mid-scan — so the Qt ``ConfigForm`` cannot bind to it directly
(``_apply`` does ``setattr`` on the bound object). This is the small mutable twin
the form edits, exactly the role ``CfgRange`` plays for the CFG calibration form.

Every field is a plain number in the routine's own units (mm, seconds, counts) so
the form can use ``FloatSpec``/``IntSpec``/``BoolSpec`` with no quantity wrapping.
:meth:`to_config` freezes the current values into the real :class:`XcorrConfig`
the routine consumes; the planner still validates every commanded setpoint against
the soft limits, so a bad range fails as a ``PlanError`` before anything moves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app_apps.routines.xcorr.config import XcorrConfig


@dataclass
class XcorrSettings:
    """The editable knobs behind the XCORR Scan panel.

    Defaults mirror ``run_xcorr_headless.py`` — known in-range values — so the
    panel opens ready to plan. Commanded positions are derived by the planner:
    probe = base + grating + ``probe_intercept_mm``; delay = base + slope*grating
    + ``delay_intercept_mm``. Only the *corrected* positions are limit-checked.
    """

    # --- probe (axis 1) — the scanned axis, BASE sweep in mm ------------------
    # The operator default: full 0→125 mm base sweep at the 0.2 mm Nyquist floor,
    # commanded = base + grating + 110. Adaptive stepping is on by default, so 0.2 mm
    # is the *finest* step and low-frequency setpoints are sampled coarsely (up to the
    # 1.0 mm cap).
    probe_start_mm: float = 0.0
    probe_stop_mm: float = 120.0
    probe_step_mm: float = 0.15
    probe_intercept_mm: float = 96.0

    # --- grating (axis 3) — chirp difference, mm -----------------------------
    grating_start_mm: float = 15.0
    grating_stop_mm: float = -75.0
    grating_step_mm: float = 10.0

    # --- delay (axis 2) — central frequency, BASE range in mm ----------------
    delay_base_start_mm: float = 0.0
    delay_base_stop_mm: float = 0.0
    delay_base_step_mm: float = 1.0
    delay_slope: float = -0.004857142857142858
    delay_intercept_mm: float = 18.585714285714285

    # --- adaptive probe step (Nyquist-matched) -------------------------------
    adaptive_probe_step: bool = True
    probe_oversample: float = 4.0
    probe_step_max_mm: float = 1.0

    # --- acquisition ---------------------------------------------------------
    n_traces: int = 3
    settle_s: float = 0.0
    timeout_s: float = 130.0
    channel: int = 1
    mock_scope: bool = False

    #: Not a form field (no Path widget) — the run directory the writer lands in.
    #: Folded into the run filename; blank gives the plain timestamped name.
    run_name: str = "scan_L_1"

    out_dir: Path = field(default_factory=lambda: Path.cwd() / "xcorr_runs")

    def to_config(self) -> XcorrConfig:
        """Freeze the current values into the routine's real config."""
        return XcorrConfig(
            probe_start_mm=self.probe_start_mm,
            probe_stop_mm=self.probe_stop_mm,
            probe_step_mm=self.probe_step_mm,
            probe_intercept_mm=self.probe_intercept_mm,
            grating_start_mm=self.grating_start_mm,
            grating_stop_mm=self.grating_stop_mm,
            grating_step_mm=self.grating_step_mm,
            delay_base_start_mm=self.delay_base_start_mm,
            delay_base_stop_mm=self.delay_base_stop_mm,
            delay_base_step_mm=self.delay_base_step_mm,
            delay_slope=self.delay_slope,
            delay_intercept_mm=self.delay_intercept_mm,
            adaptive_probe_step=self.adaptive_probe_step,
            probe_oversample=self.probe_oversample,
            probe_step_max_mm=self.probe_step_max_mm,
            n_traces=self.n_traces,
            settle_s=self.settle_s,
            timeout_s=self.timeout_s,
            channel=self.channel,
            mock_scope=self.mock_scope,
            out_dir=self.out_dir,
            run_name=self.run_name,
        )
