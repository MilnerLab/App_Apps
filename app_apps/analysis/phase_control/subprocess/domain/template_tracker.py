"""The frozen-template control loop: capture a shape, then track only its phase.

Four states, and the transitions between them are the whole design:

  OFF        The cold per-frame loop: every frame runs the full fit and corrects from it,
             i.e. exactly the behaviour that predates this module. Reached by selecting
             Fast correction.
  IDLE       Slow correction selected, but no template. The cold fit still runs so the
             chart overlay and the readouts stay live, and NO correction is issued. This
             is where the loop sits until a capture is asked for.
  CAPTURING  Collecting ``CAPTURE_N`` CONSECUTIVELY accepted traces. Holding: no correction
             is issued. A rejection resets the count to zero. Entered ONLY by an explicit
             Capture reference -- from the panel button or from a routine.
  LOCKED     A template is installed. Every frame is a 99 us closed-form phase fit against
             it; the worker averages those and corrects once every N seconds.

**A template is installed and dropped by the operator, and by nobody else.** It used to be
automatic on both ends -- Start armed a capture, and a shape-mismatch or a commanded stage
move dropped the template and armed another. Every one of those fired during ordinary work
(the move trigger at every setpoint of every scan), each cost 5 s of held correction, and
each replaced the reference with whatever the fringes happened to look like at a moment
nobody chose. The reference is a measurement the operator made deliberately; the loop does
not get to second-guess it.

So: ``request_capture`` (the panel button, or a routine through
``PhaseStabilizationHandle.capture_reference``) is the only thing that starts a capture, and
``invalidate`` -- likewise commanded, not inferred -- is the only thing that drops a
template. The shape mismatch is still measured and logged every 2 s as a diagnostic, because
it is the one number that says how far the fringes have moved from the reference; it just
does not act.

**The cold path is not touched.** Capture runs ``PhaseTracker.update`` unmodified on each of
the 10 -- same optimizer, same multi-start, same ``StabilizationConfig.accepts`` gate. It is
not made faster and not made looser.
"""
from __future__ import annotations

import enum
import logging
import time
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

# Seconds between shape-mismatch lines while LOCKED. Per frame this would be 4 lines/s of
# a number that barely moves; every 2 s it is a trend.
_MISMATCH_LOG_PERIOD_S = 2.0




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

    @property
    def coherence(self) -> float:
        """|z|: how much the frames in the window AGREED, 1 = perfectly, 0 = they cancelled.

        Worth reading before acting on ``value()``: the angle of a resultant that has
        collapsed to nothing is numerical noise, not a phase, and it is exactly what a
        window straddling a pi-ambiguous fit produces.
        """
        return float(abs(self._z))

    def value(self) -> float | None:
        """The mean phase in (-pi, pi], or None if nothing has been added."""
        if self._n == 0 or self._z == 0j:
            return None
        return float(np.angle(self._z))


class TemplateState(enum.Enum):
    OFF = "off"              # fast correction: per-frame cold fit drives the loop
    IDLE = "idle"            # slow correction, no template: cold fit for display, no
                             # correction, waiting for an explicit Capture reference
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
        self._last_mismatch_log = 0.0
        # Runs that reached 10 traces and were then thrown away by the averaged-trace gates.
        # Counted because the retry is otherwise invisible: the panel shows the counter fall
        # back to 0/10, which looks identical to a run being broken by one bad frame.
        self._abandoned = 0
        # A pinned template is one the operator recalled off disk and has not deselected.
        # Reported on the panel and cleared by Clear recall. Nothing automatic replaces any
        # template now, so this no longer guards anything -- it says where the shape came
        # from.
        self._pinned = False

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
    def abandoned_runs(self) -> int:
        """Completed runs of 10 that the averaged-trace gates rejected since the last
        successful capture. Zeroed by a fresh Capture reference and by a capture landing."""
        return self._abandoned

    @property
    def current_phase(self):
        """The cold tracker's last committed phase. OFF-mode readout only."""
        return self._cold.current_phase

    # -- commands ---------------------------------------------------------------------
    def request_capture(self) -> None:
        """Operator pressed Capture reference: collect the next 10 accepted traces.

        This is also the operator retiring a pinned recall: they asked for a fresh shape.

        Capture RETRIES until it gets an unbroken run of 10 that the averaged-trace gates
        accept. There is no attempt limit and no give-up: a capture that stopped trying
        would leave the loop holding forever with nothing on the panel to say why.
        """
        self._pinned = False
        self._abandoned = 0
        self._reset_run()
        self._state = TemplateState.CAPTURING
        log.info("template: capture requested (%d consecutive accepted traces)", CAPTURE_N)

    @property
    def pinned(self) -> bool:
        return self._pinned

    def install(self, template: PhaseTemplate, pinned: bool = False) -> None:
        """Recall a saved template, overriding whatever is installed.

        Sign continuity still applies: a template off a file is as sign-ambiguous as a freshly
        fitted one, and installing one that disagrees with the running template inverts the
        loop just as thoroughly.

        ``pinned`` marks it as recalled off disk, which the panel reports and Clear recall
        clears. It no longer changes what may replace the template -- nothing automatic
        replaces any template.
        """
        self._prev_template = self._template
        self._template = align_sign(template, self._prev_template)
        self._state = TemplateState.LOCKED
        self._pinned = bool(pinned)
        self._reset_run()
        log.info("template: installed%s (captured %s, %d px)",
                 " and pinned" if pinned else "",
                 template.captured_utc or "?", len(template.x_ref))

    def invalidate(self, reason: str) -> bool:
        """Drop the template and go IDLE. Returns True if anything actually changed.

        This is a COMMAND -- the operator, or a routine acting for them, saying the reference
        no longer describes the fringes. It is not reached from any automatic trigger: the
        stage-move and shape-mismatch triggers that used to call it are gone, because a
        loop that drops the reference on its own is a loop that quietly stops correcting in
        the middle of the work it was set up for.

        It does NOT re-arm a capture. The next reference is captured when it is asked for.
        """
        if self._state in (TemplateState.OFF, TemplateState.IDLE):
            return False
        if self._state == TemplateState.CAPTURING:
            # An explicit capture is in flight and the shape just changed under it: the
            # traces collected so far are from the OLD shape, so the run restarts. It is not
            # cancelled -- the operator asked for this capture, and only they may call it off.
            if self._run:
                log.info("template: %s during capture -- restarting the run at 0/%d",
                         reason, CAPTURE_N)
            self._reset_run()
            return False
        log.info("template: invalidated (%s) -- idle, awaiting an explicit capture", reason)
        self._prev_template = self._template or self._prev_template
        self._template = None
        self._state = TemplateState.IDLE
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
        self._pinned = False
        self._reset_run()
        return True

    def idle(self, reason: str = "slow correction selected") -> bool:
        """Slow mode with no template: run the cold fit for display, correct nothing.

        Not :meth:`request_capture` -- that is the operator asking for a shape, and it is the
        only thing that may start one. Returns True if anything changed.
        """
        if self._state == TemplateState.IDLE:
            return False
        log.info("template: idle (%s) -- no correction until a reference is captured", reason)
        self._prev_template = self._template or self._prev_template
        self._template = None
        self._state = TemplateState.IDLE
        self._pinned = False
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
        if self._state == TemplateState.IDLE:
            # The cold fit runs so the overlay, the residual readouts and the accept counter
            # stay live -- but cold_phase is deliberately not returned, so nothing here can
            # move the plate.
            committed = self._cold.update(wavelengths_nm, intensities, skipped=skipped)
            return TrackerOutcome(state=self._state, committed=committed)
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

        # DIAGNOSTIC ONLY. This used to decide whether the template survived, and that was
        # wrong twice over: the metric cannot separate a real shape change from its own live
        # spread (0.008..0.112 against a 0.12 reference, the smallest deliberate change ever
        # measured being 0.0114 -- INSIDE that spread), and acting on it meant the loop threw
        # away the operator's reference and refit on its own. The number is still worth
        # seeing, so it is still computed and logged; nothing acts on it.
        now = time.perf_counter()
        if now - self._last_mismatch_log >= _MISMATCH_LOG_PERIOD_S:
            self._last_mismatch_log = now
            mismatch = shape_mismatch(wl, inten, tpl)
            log.info("template: shape mismatch %.4f (reference %.4f, %.0f%% of it) -- "
                     "diagnostic, the template is not dropped on this",
                     mismatch, self._config.shape_mismatch_max,
                     100.0 * mismatch / max(self._config.shape_mismatch_max, 1e-12))
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
            self._abandoned += 1
            log.warning("template: capture ABANDONED (%d) -- averaged visibility %.3f < %.3f"
                        " -- retrying", self._abandoned, vis, self._config.min_visibility)
            return False

        lam_ref = self._config.params.lambda_ref.value(Prefix.NANO)
        anchor = baseline_anchor(wl_full, avg_full)
        try:
            r = analyze_trace(wl, avg, self._config.params.tunables(),
                              anchor=anchor, lambda_ref_nm=lam_ref)
        except Exception:
            self._abandoned += 1
            log.exception("template: capture fit failed (%d) -- retrying", self._abandoned)
            return False
        if not self._config.accepts(r):
            self._abandoned += 1
            log.warning("template: capture ABANDONED (%d) -- the averaged trace was rejected "
                        "[%s] rms_frac=%.3f inl=%.0f%% -- retrying",
                        self._abandoned, r.status, r.rms_frac, r.inlier_pct)
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
        self._abandoned = 0
        log.info("template: captured over %d traces, vis=%.3f, c1=%.4g, amp_ref=%.3g",
                 CAPTURE_N, vis, self._template.csig[1], self._template.amp_ref)
        return True

    def _reset_run(self) -> None:
        self._run = []
        self._run_wl = None
