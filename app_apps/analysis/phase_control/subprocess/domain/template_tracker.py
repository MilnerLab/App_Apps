"""The frozen-template control loop: capture a shape, then track only its phase.

Three states, and the transitions between them are the whole design:

  OFF        No template has ever been asked for. Every frame runs the full cold fit and
             corrects from it -- i.e. exactly the behaviour that predates this module. This
             is the state the app starts in, so nothing changes until the operator presses
             Capture reference.
  CAPTURING  Collecting ``CAPTURE_N`` CONSECUTIVELY accepted traces. Holding: no correction
             is issued. A rejection resets the count to zero. Entered by Capture reference,
             and re-entered automatically whenever a template is invalidated.
  LOCKED     A template is installed. Every frame is a 99 us closed-form phase fit against
             it; the worker averages those and corrects once every N seconds.

Recovery from an invalidation is automatic and costs ~5 s at 2 Hz. That is what makes
"stabilization keeps running through a routine" possible: the loop re-captures on a real
shape change instead of being suspended for the duration of a scan.

**The cold path is not touched.** Capture runs ``PhaseTracker.update`` unmodified on each of
the 10 -- same optimizer, same multi-start, same ``StabilizationConfig.accepts`` gate. It is
not made faster and not made looser.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from base_core.quantities.enums import Prefix

from app_apps.analysis.phase_control.subprocess.domain.fringe_fit import (
    analyze_trace,
    baseline_anchor,
)
from app_apps.analysis.phase_control.subprocess.domain.fringe_visibility import (
    fringe_visibility,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
    StabilizationConfig,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_template import (
    CAPTURE_N,
    PhaseTemplate,
    align_sign,
    fit_phase,
    instantaneous_frequency,
    shape_mismatch,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_tracker import PhaseTracker

log = logging.getLogger(__name__)


class PhaseAverager:
    """Exponentially-weighted running mean of a phase, computed on the UNIT VECTOR.

        z <- (1-g)*z + g*exp(i*phi_i);   phase = angle(z)

    It has to be circular. Phase is mod 2pi, so the arithmetic mean of 0.01 and 6.27 rad is
    pi -- the exact opposite of both inputs, and a confident instruction to drive the plate
    half a turn the wrong way. On the unit vector those two average to ~0, which is the
    answer.

    ``g`` weights recent traces more, which is the point: the loop corrects long-term drift
    and averages the fast phase noise away rather than chasing it into the stage.
    """

    def __init__(self) -> None:
        self._z = 0j
        self._n = 0

    def add(self, phase_rad: float, gain: float) -> None:
        g = min(max(float(gain), 0.0), 1.0)
        e = np.exp(1j * float(phase_rad))
        self._z = e if self._n == 0 else (1.0 - g) * self._z + g * e
        self._n += 1

    def reset(self) -> None:
        """Flush. Called on template re-capture, target change, config change, pause and
        stop -- every event after which the accumulated phases describe a different problem."""
        self._z = 0j
        self._n = 0

    @property
    def count(self) -> int:
        return self._n

    def value(self) -> float | None:
        """The mean phase in (-pi, pi], or None if nothing has been added."""
        if self._n == 0 or self._z == 0j:
            return None
        return float(np.angle(self._z))


class TemplateState(enum.Enum):
    OFF = "off"              # no template requested; per-frame cold fit drives the loop
    CAPTURING = "capturing"  # collecting an unbroken run of accepted traces; holding
    LOCKED = "locked"        # template installed; closed-form phase per frame


@dataclass(frozen=True)
class TrackerOutcome:
    """What one frame produced.

    ``phase_abs`` is the ABSOLUTE phase at ``lambda_ref`` (see
    ``PhaseTemplate.absolute_phase``) and is None on any frame that must not move the plate.
    ``cold_phase`` is set only in OFF, where the caller keeps correcting per commit as before.
    """

    state: TemplateState
    phase_abs: float | None = None
    committed: bool = False        # a cold fit committed -> config.params/overlay refreshed
    template_changed: bool = False  # installed or dropped this frame -> re-notify the UI
    cold_phase: float | None = None  # OFF only: the per-frame cold phase, mod 2pi


class TemplateTracker:
    def __init__(self, config: StabilizationConfig) -> None:
        self._config = config
        self._cold = PhaseTracker(config)
        self._state = TemplateState.OFF
        self._template: PhaseTemplate | None = None
        # Kept across a re-capture purely so align_sign has something to be continuous with.
        self._prev_template: PhaseTemplate | None = None
        self._run: list[np.ndarray] = []   # the unbroken run of accepted FULL traces
        self._run_wl: np.ndarray | None = None

    # -- state ------------------------------------------------------------------------
    @property
    def state(self) -> TemplateState:
        return self._state

    @property
    def template(self) -> PhaseTemplate | None:
        return self._template

    @property
    def capture_progress(self) -> tuple[int, int]:
        return len(self._run), CAPTURE_N

    @property
    def current_phase(self):
        """The cold tracker's last committed phase. OFF-mode readout only."""
        return self._cold.current_phase

    # -- commands ---------------------------------------------------------------------
    def request_capture(self) -> None:
        """Operator pressed Capture reference: collect the next 10 accepted traces."""
        self._reset_run()
        self._state = TemplateState.CAPTURING
        log.info("template: capture requested (%d consecutive accepted traces)", CAPTURE_N)

    def install(self, template: PhaseTemplate) -> None:
        """Recall a saved template, overriding whatever is installed.

        Sign continuity still applies: a template off a file is as sign-ambiguous as a freshly
        fitted one, and installing one that disagrees with the running template inverts the
        loop just as thoroughly.
        """
        self._prev_template = self._template
        self._template = align_sign(template, self._prev_template)
        self._state = TemplateState.LOCKED
        self._reset_run()
        log.info("template: installed (captured %s, %d px)",
                 template.captured_utc or "?", len(template.x_ref))

    def invalidate(self, reason: str) -> bool:
        """Drop the template and re-arm capture. Returns True if anything actually changed.

        Called on a commanded delay or grating move (zero lag, deterministic, and it fires
        BEFORE the bad data arrives rather than after) and on a per-trace shape mismatch. A
        probe move does not invalidate: it does not change the interferogram's shape.
        """
        if self._state == TemplateState.OFF:
            return False
        log.info("template: invalidated (%s) -- holding, re-capturing", reason)
        self._prev_template = self._template or self._prev_template
        self._template = None
        self._state = TemplateState.CAPTURING
        self._reset_run()
        return True

    def disable(self, reason: str = "fast correction selected") -> bool:
        """Drop any template and fall back to the cold per-frame loop. Returns True if
        anything changed.

        The counterpart to :meth:`request_capture`, and NOT the same as
        :meth:`invalidate`: invalidate means "this template no longer describes the
        fringes, get another one" and re-arms capture, whereas this means "stop using
        templates at all". Routing the fast/slow toggle through invalidate would leave the
        loop capturing forever and correcting never.
        """
        if self._state == TemplateState.OFF:
            return False
        log.info("template: disabled (%s) -- cold per-frame loop from here", reason)
        self._prev_template = self._template or self._prev_template
        self._template = None
        self._state = TemplateState.OFF
        self._reset_run()
        return True

    def retune(self, config: StabilizationConfig) -> None:
        """Adopt a new config. The TEMPLATE survives -- an edit to the accept gate or the
        loop gain does not change the fringe shape, and dropping it would cost a 5 s
        re-capture every time the operator touches a spinbox. The in-progress capture run
        does not: those traces were accepted under the old gate."""
        self._config = config
        self._cold = PhaseTracker(config)
        self._reset_run()

    # -- per frame --------------------------------------------------------------------
    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray,
               skipped: int = 0) -> TrackerOutcome:
        if self._state == TemplateState.OFF:
            committed = self._cold.update(wavelengths_nm, intensities, skipped=skipped)
            phase = self._cold.current_phase
            return TrackerOutcome(
                state=self._state, committed=committed,
                cold_phase=float(phase) if (committed and phase is not None) else None,
            )
        if self._state == TemplateState.LOCKED:
            return self._track(wavelengths_nm, intensities)
        return self._capture(wavelengths_nm, intensities, skipped)

    # -- locked ------------------------------------------------------------------------
    def _track(self, wl_full: np.ndarray, inten_full: np.ndarray) -> TrackerOutcome:
        tpl = self._template
        assert tpl is not None
        wl, inten = self._cold.window(wl_full, inten_full)
        if wl.size < 16:
            return TrackerOutcome(state=self._state)

        mismatch = shape_mismatch(wl, inten, tpl)
        if not np.isfinite(mismatch) or mismatch > self._config.shape_mismatch_max:
            self.invalidate(f"shape mismatch {mismatch:.4f} > {self._config.shape_mismatch_max:.4f}")
            return TrackerOutcome(state=self._state, template_changed=True)

        fit = fit_phase(wl, inten, tpl)
        # In-loop fringe-strength gate. The correlation amplitude falls ~226x when the
        # fringes wash out, so this is the cheap in-loop equivalent of the visibility index
        # -- which is why that 1.9 ms metric is only needed to protect capture. Relative to
        # the capture amplitude, because the absolute value scales with the trace brightness.
        if tpl.amp_ref > 0.0 and fit.amplitude < self._config.min_amplitude_frac * tpl.amp_ref:
            log.info("template: holding, fringe amplitude %.3g < %.0f%% of reference %.3g",
                     fit.amplitude, 100.0 * self._config.min_amplitude_frac, tpl.amp_ref)
            return TrackerOutcome(state=self._state)

        lam_ref = self._config.params.lambda_ref.value(Prefix.NANO)
        return TrackerOutcome(state=self._state,
                              phase_abs=tpl.absolute_phase(fit.delta, lam_ref))

    # -- capturing ----------------------------------------------------------------------
    def _capture(self, wl_full: np.ndarray, inten_full: np.ndarray,
                 skipped: int) -> TrackerOutcome:
        # The FULL cold pipeline, per trace, unchanged -- including the visibility gate and
        # StabilizationConfig.accepts. A rejection breaks the run.
        if not self._cold.update(wl_full, inten_full, skipped=skipped):
            if self._run:
                log.info("template: capture run broken at %d/%d", len(self._run), CAPTURE_N)
            self._reset_run()
            return TrackerOutcome(state=self._state)

        wl = np.asarray(wl_full, float)
        if self._run_wl is None or self._run_wl.shape != wl.shape:
            # A grid change (the spectrometer was reconfigured) makes the accumulated traces
            # unaveragable. Start the run again rather than average across two grids.
            self._reset_run()
            self._run_wl = wl
        self._run.append(np.asarray(inten_full, float))
        if len(self._run) < CAPTURE_N:
            return TrackerOutcome(state=self._state, committed=True)

        installed = self._build_template()
        self._reset_run()
        return TrackerOutcome(state=self._state, committed=True, template_changed=installed)

    def _build_template(self) -> bool:
        """Average the run, fit it once with the cold pipeline, and install the result."""
        wl_full = self._run_wl
        assert wl_full is not None
        avg_full = np.mean(np.stack(self._run), axis=0)
        wl, avg = self._cold.window(wl_full, avg_full)

        # The average is where a phase drift across the run would show up: drifting fringes
        # average AWAY, leaving a clean bump the cold fit would happily fit to noise and the
        # loop would then trust indefinitely. Consecutiveness makes that unlikely; this makes
        # it impossible.
        vis = fringe_visibility(avg)
        if vis < self._config.min_visibility:
            log.warning("template: capture ABANDONED -- averaged visibility %.3f < %.3f",
                        vis, self._config.min_visibility)
            return False

        lam_ref = self._config.params.lambda_ref.value(Prefix.NANO)
        anchor = baseline_anchor(wl_full, avg_full)
        try:
            r = analyze_trace(wl, avg, self._config.params.tunables(),
                              anchor=anchor, lambda_ref_nm=lam_ref)
        except Exception:
            log.exception("template: capture fit failed")
            return False
        if not self._config.accepts(r):
            log.warning("template: capture ABANDONED -- the averaged trace was rejected "
                        "[%s] rms_frac=%.3f inl=%.0f%%", r.status, r.rms_frac, r.inlier_pct)
            return False

        tpl = PhaseTemplate(
            l0=float(r.l0),
            csig=[float(c) for c in r.csig],
            pU=[float(c) for c in r.pU],
            pLn=[float(c) for c in r.pLn],
            x_ref=[float(v) for v in wl],
            captured_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # integration_ms / averages are stamped main-side by PhaseStabilizationHandle:
            # the spectrometer settings live on SpectrometerWorkerHandle.config, which this
            # subprocess has no access to.
        )
        tpl.f_ref = [float(v) for v in instantaneous_frequency(wl, avg, tpl)]
        # Amplitude of the template's own fit against the trace it was built from: the
        # reference the in-loop strength gate is a fraction of. Invariant under align_sign
        # (hypot(C, S) does not care that S changes sign), so it can be measured before it.
        tpl.amp_ref = fit_phase(wl, avg, tpl).amplitude

        self._prev_template = self._template or self._prev_template
        self._template = align_sign(tpl, self._prev_template)
        self._state = TemplateState.LOCKED
        log.info("template: captured over %d traces, vis=%.3f, c1=%.4g, amp_ref=%.3g",
                 CAPTURE_N, vis, self._template.csig[1], self._template.amp_ref)
        return True

    def _reset_run(self) -> None:
        self._run = []
        self._run_wl = None
