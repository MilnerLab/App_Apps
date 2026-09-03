"""Capture a shape cold, then track only its phase.

Two states, and nothing moves between them but the operator:

  CAPTURING  Collecting ``capture_n`` CONSECUTIVELY accepted traces with the full cold
             pipeline -- every parameter free. A rejection resets the run. When the run
             completes, the averaged trace is fit once, the fitted shape is FROZEN, and the
             phase it measures becomes the target. No correction is issued while capturing.
  LOCKED     The shape is frozen. Every frame is a ~99 us closed-form fit of ONE parameter,
             the phase, against it.

Why the tracking fit is not cold: a cold fit re-solves the envelope, the carrier and the
chirp on every frame, so the phase it reports is measured against a slightly different model
each time and the frame-to-frame scatter is the model's, not the light's. Freezing everything
but the phase makes the measurement deterministic and repeatable, which is the property a
control loop needs and the only reason the block average means anything.

Recapture is MANUAL and is the whole re-referencing story: it refits every parameter cold and
re-zeros the phase, which is what a centrifuge change calls for. There is no automatic
invalidation, no shape-mismatch backstop and no auto-recapture -- a template is dropped when,
and only when, someone asks for it.
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
    PhaseTemplate,
    align_sign,
    fit_phase,
    instantaneous_frequency,
    shape_mismatch,
)
from app_apps.analysis.phase_control.subprocess.domain.phase_tracker import PhaseTracker

log = logging.getLogger(__name__)

# Seconds between LOCKED-path diagnostic lines. The tracked fit runs at the frame rate and
# is silent by design; this prints one line every so often so a loop that is "running" but
# not actually measuring anything reads as numbers rather than as a still counter.
_TRACK_LOG_PERIOD_S = 2.0


class TrackerState(enum.Enum):
    CAPTURING = "capturing"  # cold full fits, collecting an unbroken accepted run
    LOCKED = "locked"        # shape frozen; one closed-form phase per frame


@dataclass(frozen=True)
class TrackerOutcome:
    """What one frame produced.

    ``phase_abs`` is the ABSOLUTE phase at ``lambda_ref`` (see
    ``PhaseTemplate.absolute_phase``) and is None on any frame that must not feed the loop.
    ``target_phase`` is set on exactly one frame: the one that completes a capture.
    """

    state: TrackerState
    phase_abs: float | None = None
    delta: float = 0.0                 # phase offset from the FROZEN polynomial, in (-pi, pi].
                                       # Carried so the overlay can be redrawn at the phase
                                       # actually measured: in LOCKED no cold fit commits, so
                                       # without this the chart would freeze at the capture
                                       # while the real fringes walked away from it.
    committed: bool = False            # a cold fit committed -> config.params/overlay refreshed
    target_phase: float | None = None  # a capture completed; adopt this as the setpoint


class StabilizationTracker:
    def __init__(self, config: StabilizationConfig) -> None:
        self._config = config
        self._cold = PhaseTracker(config)
        self._template: PhaseTemplate | None = None
        self._state = TrackerState.CAPTURING
        self._run: list[np.ndarray] = []
        self._run_wl: np.ndarray | None = None
        self._last_track_log = 0.0
        # The pinned phase reference (nm), or None before the first capture. See
        # StabilizationConfig.pinned_lambda_ref.
        self._lam_pin: float | None = None

    # --- observation -------------------------------------------------------------------
    @property
    def state(self) -> TrackerState:
        return self._state

    @property
    def template(self) -> PhaseTemplate | None:
        return self._template

    @property
    def capture_progress(self) -> tuple[int, int]:
        return len(self._run), int(self._config.capture_n)

    # --- commands ----------------------------------------------------------------------
    def request_capture(self) -> None:
        """Refit every parameter cold and re-zero the phase. The manual re-reference.

        The existing template is deliberately KEPT installed until the new one is built: a
        capture can be abandoned (a broken run, washed-out fringes), and dropping the working
        shape at the moment the operator asks for a better one would leave the loop with
        nothing if the new capture never lands.
        """
        self._reset_run()
        self._state = TrackerState.CAPTURING

    def retune(self, config: StabilizationConfig) -> None:
        """Adopt an edited config. A config edit does NOT drop the frozen reference -- with
        one exception, and this is it.

        Moving the ROI moves the SAMPLES the frozen template is fit against, and the template
        is only meaningful on the ones it was built from: ``amp_ref`` is measured over that
        span, so tracking a narrower one drops the amplitude ratio below
        ``min_amplitude_frac`` and the loop holds forever, while tracking a wider one
        evaluates the frozen polynomial where it was never fit. Neither failure announces
        itself. So an ROI change forces a re-capture -- the same thing the operator would
        have to do by hand, done for them rather than after they lost an hour to it.
        """
        roi_was = self._config.roi
        self._config = config
        self._cold.retune(config)
        if config.roi != roi_was:
            log.info("ROI changed %s -> %s: re-capturing the reference",
                     roi_was, config.roi)
            self.request_capture()

    def update(self, wavelengths_nm: np.ndarray, intensities: np.ndarray,
               skipped: int = 0) -> TrackerOutcome:
        if self._state == TrackerState.LOCKED:
            return self._track(wavelengths_nm, intensities)
        return self._capture(wavelengths_nm, intensities, skipped)

    # --- locked ------------------------------------------------------------------------
    def _track(self, wl_full: np.ndarray, inten_full: np.ndarray) -> TrackerOutcome:
        tpl = self._template
        assert tpl is not None
        # The ROI, when there is one -- the template was fit there and is only supported
        # there. See PhaseTracker.roi_window.
        wl, inten = self._cold.roi_window(wl_full, inten_full)
        if wl.size < 16:
            return TrackerOutcome(state=self._state)

        fit = fit_phase(wl, inten, tpl)
        now = time.perf_counter()
        if now - self._last_track_log >= _TRACK_LOG_PERIOD_S:
            self._last_track_log = now
            frac = fit.amplitude / tpl.amp_ref if tpl.amp_ref > 0.0 else float("nan")
            log.info("TRACK: n=%d (%.2f-%.2f nm) delta=%+.4f rad  amp=%.3g (%.2fx ref)"
                     "  mismatch=%.4f  vis=%.4f",
                     wl.size, float(wl[0]), float(wl[-1]), fit.delta, fit.amplitude, frac,
                     shape_mismatch(wl, inten, tpl), fringe_visibility(inten))
        # The in-loop quality gate, and the direct descendant of the legacy
        # residuals_threshold: below this the fringes have washed out and the closed-form
        # phase is a confident number fit to noise. Relative to the capture amplitude,
        # because the absolute value scales with how bright the trace is.
        if tpl.amp_ref > 0.0 and fit.amplitude < self._config.min_amplitude_frac * tpl.amp_ref:
            log.info("holding: fringe amplitude %.3g < %.0f%% of reference %.3g",
                     fit.amplitude, 100.0 * self._config.min_amplitude_frac, tpl.amp_ref)
            return TrackerOutcome(state=self._state)

        # The template's OWN pinned reference, not the config's current value: the phase the
        # loop tracks has to be measured at the same wavelength the setpoint was taken at,
        # and editing lambda_ref mid-run must not silently re-zero the loop.
        lam_ref = tpl.lambda_ref or self._config.params.lambda_ref.value(Prefix.NANO)
        return TrackerOutcome(state=self._state, delta=fit.delta,
                              phase_abs=tpl.absolute_phase(fit.delta, lam_ref))

    # --- capturing ---------------------------------------------------------------------
    def _capture(self, wl_full: np.ndarray, inten_full: np.ndarray,
                 skipped: int) -> TrackerOutcome:
        # The FULL cold pipeline, per trace, unchanged -- including the visibility gate and
        # StabilizationConfig.accepts. A rejection breaks the run.
        # Under an ROI there is no rejection left to break the run (see
        # StabilizationConfig.accepts), so "capture_n CONSECUTIVELY accepted traces" becomes
        # plain N-frame averaging. That is the deliberate cost of the override: the
        # consecutiveness existed to stop a reference being averaged across a disturbance,
        # and the operator now carries that judgement along with the rest.
        n_needed = int(self._config.capture_n)
        wl_w, in_w = self._cold.window(wl_full, inten_full)
        now = time.perf_counter()
        if now - self._last_track_log >= _TRACK_LOG_PERIOD_S:
            self._last_track_log = now
            log.info("CAPTURE: window n=%d (%.2f-%.2f nm of %d full)  vis=%.4f  run=%d/%d",
                     wl_w.size, float(wl_w[0]) if wl_w.size else float("nan"),
                     float(wl_w[-1]) if wl_w.size else float("nan"), wl_full.size,
                     fringe_visibility(in_w) if in_w.size else float("nan"),
                     len(self._run), n_needed)
        if not self._cold.update(wl_full, inten_full, skipped=skipped):
            if self._run:
                log.info("capture run broken at %d/%d", len(self._run), n_needed)
            self._reset_run()
            return TrackerOutcome(state=self._state)

        wl = np.asarray(wl_full, float)
        if self._run_wl is None or self._run_wl.shape != wl.shape:
            # A grid change (the spectrometer was reconfigured) makes the accumulated traces
            # unaveragable. Start the run again rather than average across two grids.
            self._reset_run()
            self._run_wl = wl
        self._run.append(np.asarray(inten_full, float))
        if len(self._run) < n_needed:
            return TrackerOutcome(state=self._state, committed=True)

        target = self._build_template()
        self._reset_run()
        return TrackerOutcome(state=self._state, committed=True, target_phase=target)

    def _build_template(self) -> float | None:
        """Average the run, fit it once cold, freeze it, and return the phase it measures.

        The returned phase becomes the setpoint, so the loop starts at exactly zero error --
        which is what "re-zero the phase" means. None on an abandoned capture; the previous
        template (if any) stays installed and the run simply starts over.
        """
        wl_full = self._run_wl
        assert wl_full is not None
        n_run = len(self._run)
        avg_full = np.mean(np.stack(self._run), axis=0)
        wl, avg = self._cold.window(wl_full, avg_full)

        # The average is where a phase drift across the run would show up: drifting fringes
        # average AWAY, leaving a clean bump the cold fit would happily fit to noise and the
        # loop would then trust indefinitely. Consecutiveness makes that unlikely; this makes
        # it impossible.
        roi = self._config.roi
        vis = fringe_visibility(avg)
        if roi is None and vis < self._config.min_visibility:
            log.warning("capture ABANDONED -- averaged visibility %.3f < %.3f",
                        vis, self._config.min_visibility)
            return None

        # PINNED at capture and frozen into the template: the reference is re-derived only
        # when the ROI excludes both lambda_ref AND the pin in force, and then it becomes the
        # ROI midpoint. A reference that moved per frame would redefine zero underneath the
        # loop -- which is the same hazard absolute_phase() exists to close, arriving by a
        # different door.
        lam_ref = self._config.pinned_lambda_ref(self._lam_pin)
        self._lam_pin = lam_ref
        anchor = baseline_anchor(wl_full, avg_full)
        try:
            r = analyze_trace(wl, avg, self._config.params.tunables(),
                              anchor=anchor, lambda_ref_nm=lam_ref, roi=roi)
        except Exception:
            log.exception("capture fit failed")
            return None
        if not self._config.accepts(r):
            log.warning("capture ABANDONED -- the averaged trace was rejected "
                        "[%s] rms_frac=%.3f inl=%.0f%%", r.status, r.rms_frac, r.inlier_pct)
            return None
        if roi is not None:
            # Gates became readouts (they are in the panel); say the same thing in the log,
            # so a capture taken under an override is not silent about what it declined to
            # enforce.
            log.info("capture under ROI %.2f-%.2f nm: trust_ok=%s rms_frac=%.3f inl=%.0f%% "
                     "vis=%.3f (advisory, not gates)",
                     roi[0], roi[1], r.trust_ok, r.rms_frac, r.inlier_pct, vis)

        # The template is tracked on the ROI, so everything measured FROM it -- the
        # reference instantaneous frequency, amp_ref, and the setpoint -- has to be measured
        # on the same samples the tracking fit will use. amp_ref in particular is the
        # denominator of the in-loop strength gate: measuring it over a wider span than the
        # tracked one biases every later frame low and the loop holds forever.
        wl_t, avg_t = (wl, avg) if roi is None else (
            wl[(wl >= roi[0]) & (wl <= roi[1])],
            avg[(wl >= roi[0]) & (wl <= roi[1])],
        )
        if wl_t.size < 16:
            log.warning("capture ABANDONED -- only %d points to track on", wl_t.size)
            return None

        tpl = PhaseTemplate(
            lambda_ref=float(lam_ref),
            l0=float(r.l0),
            csig=[float(c) for c in r.csig],
            pU=[float(c) for c in r.pU],
            pLn=[float(c) for c in r.pLn],
            x_ref=[float(v) for v in wl_t],
            captured_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        tpl.f_ref = [float(v) for v in instantaneous_frequency(wl_t, avg_t, tpl)]
        # Amplitude of the template's own fit against the trace it was built from: the
        # reference the in-loop strength gate is a fraction of. Invariant under a sign flip
        # (hypot(C, S) does not care that S changes sign), so it can be measured before one.
        tpl.amp_ref = fit_phase(wl_t, avg_t, tpl).amplitude

        tpl = self._fix_sign(tpl)
        self._template = tpl
        self._state = TrackerState.LOCKED
        # Publish the AVERAGED fit as the committed params, replacing the last individual
        # trace's. The overlay must draw the shape the loop is actually tracking against, and
        # from here on nothing else will commit -- the tracking fit solves one parameter and
        # never touches these.
        self._config.params.commit(r, float(r.phase_at(lam_ref)))

        # The setpoint is the phase of the trace the template was built FROM, so the first
        # tracked frame reads ~0 error rather than an arbitrary offset.
        target = tpl.absolute_phase(fit_phase(wl_t, avg_t, tpl).delta, lam_ref)
        log.info("captured over %d traces, vis=%.3f, c1=%.4g, amp_ref=%.3g, target=%.3f rad",
                 n_run, vis, tpl.csig[1], tpl.amp_ref, target)
        return float(target)

    def _fix_sign(self, tpl: PhaseTemplate) -> PhaseTemplate:
        """Pin the sign of the frozen phase, across captures AND across app launches.

        The cold fit is sign-ambiguous -- the model is ``mid + half*cos(Phi)`` and cosine is
        even, so ``Phi -> -Phi`` is a bit-identical fit and which one the optimiser lands on
        is a seed accident. An inverted template inverts the LOOP: the measured error changes
        sign and the correction drives away from the setpoint instead of towards it.

        ``align_sign`` keeps each capture consistent with the one before it, which is what
        makes the operator's ``invert_correction`` stay valid across a re-capture. But it is
        only a RELATIVE guard -- with no predecessor it returns the template unchanged, so
        the first capture after every launch was a coin flip and ``invert_correction`` had to
        be re-decided against a loop that had already been let go. Fixing ``c1 > 0`` when
        there is no predecessor makes that first capture deterministic too, so the toggle is
        set once for the optics and then stays put.

        Near c1 = 0 the convention is arbitrary -- but so is the fit's own sign there, and a
        carrier that weak has no phase worth tracking anyway.
        """
        if self._template is not None and not self._template.is_empty():
            return align_sign(tpl, self._template)
        if float(tpl.csig[1]) < 0.0:
            tpl.csig = [-c for c in tpl.csig]
        return tpl

    def _reset_run(self) -> None:
        self._run = []
        self._run_wl = None
