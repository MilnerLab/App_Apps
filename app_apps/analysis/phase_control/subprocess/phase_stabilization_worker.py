from __future__ import annotations

import logging
import math
import time
from typing import Callable, TYPE_CHECKING

import numpy as np

from base_core.ipc.threaded_worker import ThreadedWorker, worker_thread
from base_core.math.models import Angle
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_corrector import PhaseCorrector
from app_apps.analysis.phase_control.subprocess.domain.phase_template import PhaseTemplate
from app_apps.analysis.phase_control.subprocess.domain.template_tracker import (
    PhaseAverager,
    TemplateState,
    TemplateTracker,
)
from app_apps.analysis.phase_control.subprocess.messages import (
    CaptureReference,
    CorrectionAvailable,
    ConfigSynced,
    InvalidateTemplate,
    ProcessSpectrum,
    CorrectionStatus,
    RunningPhaseMean,
    RecallReference,
    SetStabilizationConfig,
    SpectrumProcessed,
    TemplateStateChanged,
)

_TWO_PI = 2.0 * math.pi

# The correction the loop issues in LOCKED mode drives the averaged phase error to ZERO in
# one move, i.e. gain 1. That is not the aggressive choice it looks like: the cold loop's
# 0.05 is per FRAME, and this fires once per correction_period_s (~30 frames) off a
# circularly averaged error, so the noise has already been taken out of it. config.loop_gain
# is not unused in this mode -- it is the weight in that average.
_TEMPLATE_CORRECTION_GAIN = 1.0
# Seconds between running-mean readouts to the panel. The fit runs at the full frame rate
# and this is only a curve on a chart; 4 Hz is smooth to the eye and cheap on the bus.
_MEAN_PUBLISH_PERIOD_S = 0.25

if TYPE_CHECKING:
    from base_core.framework.events.event_bus import EventBus
    from base_core.ipc.subprocess_connector import SubprocessPipelineConnector
    from spm_002.buffer import SpectrumBuffer

log = logging.getLogger(__name__)

WORKER_ID = "phase_tracking"
CONSUMER_ID = "phase_tracking"


class PhaseStabilizationWorker(ThreadedWorker):
    def __init__(
        self,
        bus: EventBus,
        connector: SubprocessPipelineConnector,
        config: StabilizationConfig,
        get_buffer: Callable[[], SpectrumBuffer],
    ) -> None:
        super().__init__(WORKER_ID, bus, connector)
        self._config = config
        self._get_buffer = get_buffer
        self._tracker: TemplateTracker | None = None
        self._corrector: PhaseCorrector | None = None
        # LOCKED-mode loop state: the circular running mean of the per-frame phases, and when
        # the last correction went out.
        self._averager = PhaseAverager()
        self._last_correction = time.perf_counter()
        self._last_mean_publish = 0.0
        self._paused = True
        self._latest_item_id = -1     # newest arrival (drop-stale coalescing)
        self._skipped_since_fit = 0   # frames coalesced away since the last real fit
        # --- throughput diagnostics (periodic THROUGHPUT log) ---
        self._tp_t0 = time.perf_counter()
        self._tp_fit = 0              # frames actually fit in the window
        self._tp_skip = 0            # frames coalesced/dropped in the window
        self._tp_commit = 0          # fits that passed the gate in the window
        self._tp_fit_ms = 0.0        # summed fit wall time in the window
        # Last (state, captured, needed) put on the wire, so the per-frame publish below
        # can stay silent while nothing moves. None = nothing published yet.
        self._last_state_stamp: tuple | None = None
        # THE REFERENCE, kept OUTSIDE the tracker so it survives the tracker being rebuilt
        # -- which is what Stop/Start does, and what a routine restarting stabilization does.
        # It holds the most recent reference however it arrived: recalled off a file (pinned)
        # or captured from live traces (not pinned). Without this, Start threw the operator's
        # reference away and the loop came back up with nothing installed.
        self._seed_template: PhaseTemplate | None = None
        self._seed_pinned = False
        # A Capture reference pressed while stabilization is STOPPED. Held rather than
        # dropped: there is no tracker to arm yet, and silently discarding the press leaves
        # the operator watching a loop that never captures and never says why.
        self._capture_pending = False

    def _setup(self) -> None:
        self._unsubs.append(self._bus.subscribe(SetStabilizationConfig, self._on_set_config))
        self._unsubs.append(self._bus.subscribe(ProcessSpectrum, self._on_spectrum))
        self._unsubs.append(self._bus.subscribe(CaptureReference, self._on_capture_reference))
        self._unsubs.append(self._bus.subscribe(RecallReference, self._on_recall_reference))
        self._unsubs.append(self._bus.subscribe(InvalidateTemplate, self._on_invalidate_template))

    def _start(self) -> None:
        self._build_tracker()
        self._latest_item_id = -1
        self._skipped_since_fit = 0
        self._paused = False

    def _pause(self) -> None:
        self._paused = True
        # Flush the running phase mean: it describes a window the loop was not acting on.
        self._averager.reset()

    def _resume(self) -> None:
        self._paused = False

    def _stop(self) -> None:
        self._build_tracker()
        self._latest_item_id = -1
        self._skipped_since_fit = 0

    def _build_tracker(self) -> None:
        self._tracker = TemplateTracker(self._config)
        self._corrector = PhaseCorrector()
        self._corrector.target_phase = self._config.set_phase
        self._corrector.gain = self._config.loop_gain
        self._corrector.invert = self._config.invert_correction
        self._averager.reset()
        self._last_correction = time.perf_counter()
        self._last_state_stamp = None   # a fresh tracker re-announces itself unconditionally
        self._corrector.deadband = self._config.correction_deadband_rad
        # Start does NOT capture. A 10-trace refit is only ever started by an explicit
        # Capture reference -- from the panel or from a routine -- because one that fires on
        # its own replaces the shape the loop is holding against at a moment nobody chose.
        # So slow mode with no template starts IDLE: the cold fit runs for the display, and
        # nothing moves the plate. A recalled template is installed and locks at once.
        if self._config.slow_correction and self._tracker is not None:
            if self._seed_template is not None:
                self._tracker.install(self._seed_template, pinned=self._seed_pinned)
            elif self._capture_pending:
                self._capture_pending = False
                self._tracker.request_capture()
            else:
                self._tracker.idle("stabilization started without a reference")
        self._publish_template_state()

    def _on_spectrum(self, msg: ProcessSpectrum) -> None:
        # Runs on the connector poll thread: record the newest arrival for
        # drop-stale coalescing, then dispatch the (serial) fit onto the worker
        # thread. The heavy fit never blocks the poll thread.
        self._latest_item_id = msg.item_id
        self._runner.run(
            lambda: self._process_spectrum(msg),
            on_error=lambda e: log.exception("PhaseStabilizationWorker: dispatch error"),
        )

    def _process_spectrum(self, msg: ProcessSpectrum) -> None:
        # Worker thread, serial. A running fit is never interrupted, so an
        # in-progress cold attempt always completes.
        #
        # THE SLOT IS ACKED BEFORE THE FIT, NOT AFTER. This worker is a registered
        # SlotCoordinator consumer, so until it acks there is no SpectrumAvailable and every
        # other consumer -- the live plot included -- stalls. Acking after the fit is why a
        # slow fit froze the APPLICATION and not just the loop: a 47 s washed-out fit took
        # the UI down with it. So: copy the arrays out of the shared buffer, ack, then fit
        # off the copies. The staleness that admits is already handled by the drop-stale
        # coalescing below (_latest_item_id), and spectrum_recorder.py does exactly this.
        acked = False

        def ack() -> None:
            # One ack per message, from whichever path gets there first: the early ack below
            # on the fitting path, or the finally-block backstop on every other path
            # (paused, dropped stale, or an exception raised before the ack).
            nonlocal acked
            if acked:
                return
            acked = True
            self._notify(SpectrumProcessed(slot=msg.slot, item_id=msg.item_id,
                                           consumer_id=CONSUMER_ID))

        try:
            if self._paused or self._tracker is None or self._corrector is None:
                return
            # Drop-stale: if a newer spectrum arrived while this one queued, skip
            # the fit (still acked below) so we only ever fit the freshest frame.
            if msg.item_id != self._latest_item_id:
                self._skipped_since_fit += 1
                self._tp_skip += 1
                return
            buf = self._get_buffer()
            # np.array(..., copy=True): the slot is released on the next line and the buffer
            # will be overwritten under us. A view would be fit against whatever landed next.
            wl = np.array(buf.wavelengths(msg.slot), dtype=float)
            ins = np.array(buf.intensities(msg.slot), dtype=float)
            ack()
            skipped = self._skipped_since_fit
            self._skipped_since_fit = 0
            t_fit0 = time.perf_counter()
            outcome = self._tracker.update(wl, ins, skipped=skipped)
            committed = outcome.committed
            # Throughput accounting: distinguishes "spectra arrive slowly" (upstream
            # acquisition/IPC) from "fits are slow" (compute) from "nothing commits"
            # (the accept gate). Summary emitted every ~2 s.
            self._tp_fit += 1
            self._tp_fit_ms += (time.perf_counter() - t_fit0) * 1e3
            self._tp_commit += int(committed)
            now = time.perf_counter()
            if now - self._tp_t0 >= 2.0:
                dt = now - self._tp_t0
                log.warning("THROUGHPUT: %.2f frames/s in (%d fit + %d coalesced over %.1fs) | "
                            "%d committed | mean fit %.0f ms",
                         (self._tp_fit + self._tp_skip) / dt, self._tp_fit, self._tp_skip,
                         dt, self._tp_commit, self._tp_fit_ms / max(self._tp_fit, 1))
                self._tp_t0 = now
                self._tp_fit = self._tp_skip = self._tp_commit = 0
                self._tp_fit_ms = 0.0
            if committed:
                self._notify(ConfigSynced(config=self._config))
            if outcome.template_changed:
                self._averager.reset()   # a new template redefines what the mean is OF
                # A capture that just landed becomes THE reference: held outside the tracker
                # so Stop/Start restores it rather than coming back up empty. Not pinned --
                # pinned means "recalled off a file", which is what the panel colours yellow.
                if outcome.state == TemplateState.LOCKED and self._tracker.template is not None:
                    self._seed_template = self._tracker.template
                    self._seed_pinned = self._tracker.pinned
            # Publish on every CHANGE of (state, progress), not only when a template
            # appears. The capture run advances 1..9 with template_changed False, and an
            # ABANDONED run resets to 0 with it False too -- so gating the notify on it
            # left the panel frozen at the 0/10 it was armed with, whether the loop was
            # counting up perfectly or restarting forever. Those two look identical to the
            # operator, which is precisely the thing the counter exists to tell apart.
            self._publish_template_state()
            if outcome.state == TemplateState.OFF:
                # Unchanged cold loop: correct from each committed fit, gain per frame.
                if outcome.cold_phase is not None:
                    self._emit_correction(Angle(outcome.cold_phase % _TWO_PI),
                                          self._config.loop_gain)
            elif outcome.phase_abs is not None:
                self._accumulate(outcome.phase_abs)
        except Exception:
            log.exception("PhaseStabilizationWorker: error processing spectrum slot %d", msg.slot)
        finally:
            ack()

    # ------------------------------------------------------------------ template loop --
    def _accumulate(self, phase_abs: float) -> None:
        """Fold one closed-form phase into the running mean, and correct when due."""
        assert self._corrector is not None
        self._averager.add(phase_abs, self._config.loop_gain)
        now = time.perf_counter()
        # The panel draws the averaged trace from this, so it has to arrive while the mean
        # is being ACCUMULATED, not once per correction period -- a curve that steps once
        # every 10 s is not the thing the loop is working on, it is a snapshot of it.
        # Rate-limited because the fit runs at the full frame rate and this is a readout.
        if now - self._last_mean_publish >= _MEAN_PUBLISH_PERIOD_S:
            self._last_mean_publish = now
            mean_now = self._averager.value()
            if mean_now is not None:
                self._notify(RunningPhaseMean(mean_phase_rad=mean_now,
                                              frames=self._averager.count,
                                              coherence=self._averager.coherence))
        if now - self._last_correction < self._config.correction_period_s:
            return
        self._last_correction = now
        mean = self._averager.value()
        if mean is None:
            self._notify(CorrectionStatus(reason="no accepted frames since the last one",
                                          period_s=self._config.correction_period_s))
            return
        n = self._averager.count
        self._averager.reset()
        log.info("template: correcting on the mean of %d frames, phi=%.3f rad", n, mean)
        self._emit_correction(Angle(mean % _TWO_PI), _TEMPLATE_CORRECTION_GAIN, frames=n)

    def _emit_correction(self, phase: Angle, gain: float, frames: int = 1) -> None:
        assert self._corrector is not None
        # Set per call rather than at construction: the two modes correct at different
        # cadences and so need different gains, and the corrector is the one place the gain
        # is clamped before it can reach the stage.
        self._corrector.gain = gain
        error = float(Angle(phase - self._corrector.target_phase).Rad)
        result = self._corrector.update(phase)
        if result is not None:
            self._notify(CorrectionAvailable(angle=result.angle, sign=result.sign))
        # Reported either way: a correction WITHHELD because the error was inside the
        # deadband is exactly what the panel needs to show, and from outside it is
        # indistinguishable from a dead loop unless it is said out loud.
        self._notify(CorrectionStatus(
            phase_error_rad=error,
            commanded_deg=0.0 if result is None else float(result.angle.Deg),
            applied=result is not None,
            reason="" if result is not None else
                   (f"error {abs(error):.3f} rad is inside the "
                    f"{self._corrector.deadband.Rad:.3f} rad deadband"),
            period_s=self._config.correction_period_s,
            frames=frames,
        ))

    def _publish_template_state(self) -> None:
        """Notify the UI of (state, capture progress), but only when it has changed.

        Called per frame from the fit path, so it has to be cheap and quiet: an unchanged
        LOCKED state must not put an IPC message on the wire at the frame rate.
        """
        if self._tracker is None:
            return
        got, need = self._tracker.capture_progress
        stamp = (self._tracker.state, got, need, self._tracker.pinned,
                 self._tracker.abandoned_runs)
        if stamp == self._last_state_stamp:
            return
        self._last_state_stamp = stamp
        self._notify(TemplateStateChanged(
            state=self._tracker.state.value, captured=got, needed=need,
            template=self._tracker.template, pinned=self._tracker.pinned,
            abandoned=self._tracker.abandoned_runs,
        ))

    @worker_thread
    def _on_capture_reference(self, msg: CaptureReference) -> None:
        # Capture is the operator asking for a FRESH shape, so it retires whatever reference
        # is held: otherwise the next Start would silently resurrect the one being replaced.
        # The capture that lands installs itself as the new one (see _process_spectrum).
        self._seed_template = None
        self._seed_pinned = False
        if self._tracker is not None:
            self._tracker.request_capture()
            self._averager.reset()
            self._publish_template_state()
        else:
            # Pressed before Start: run it when there is something to run it on.
            self._capture_pending = True
        self._reply_ok(msg)

    @worker_thread
    def _on_recall_reference(self, msg: RecallReference) -> None:
        # Remembered whether or not a tracker exists right now: Recall is usable while
        # stabilization is stopped, and that is in fact when it is normally used.
        # template=None is the operator deselecting the recall.
        self._seed_template = msg.template
        self._seed_pinned = msg.template is not None
        if self._tracker is not None:
            if msg.template is not None:
                self._tracker.install(msg.template, pinned=True)
            elif self._config.slow_correction:
                # Deselected while running: back to where Start would have left it, which is
                # IDLE. Deselecting a recall is not a request for a new capture.
                self._tracker.idle("recall deselected")
            self._averager.reset()
            self._publish_template_state()
        self._reply_ok(msg)

    def _on_invalidate_template(self, msg: InvalidateTemplate) -> None:
        # Runs on the poll thread and touches only the tracker's state flags -- deliberately
        # NOT dispatched onto the worker thread. A commanded move must invalidate BEFORE the
        # corrupted spectra are fit, and the worker thread may be mid-fit for hundreds of ms.
        if self._tracker is not None and self._tracker.invalidate(msg.reason):
            self._averager.reset()
            self._publish_template_state()

    @worker_thread
    def _on_set_config(self, msg: SetStabilizationConfig) -> None:
        self._config = msg.config
        # A config change can move lambda_ref, the window or the target, so the accumulated
        # phases no longer describe the same quantity. The TEMPLATE survives: the shape did
        # not change, and throwing it away would cost a 5 s re-capture on every edit.
        self._averager.reset()
        if self._tracker is not None:
            self._tracker.retune(self._config)
            # The fast/slow toggle lives in the config, so it arrives here. Acting on it
            # only when it actually differs from the running state keeps every other edit
            # -- a gain nudge, a target change -- from re-arming a capture and dropping a
            # good template for 5 s.
            if self._config.slow_correction:
                if self._tracker.state == TemplateState.OFF:
                    # Same precedence as _build_tracker, and the same rule: switching to slow
                    # does not start a capture.
                    if self._seed_template is not None:
                        self._tracker.install(self._seed_template, pinned=self._seed_pinned)
                    else:
                        self._tracker.idle()
                    self._publish_template_state()
            elif self._tracker.disable():
                self._averager.reset()
                self._publish_template_state()
        if self._corrector is not None:
            # Retuned in place, not reconstructed: gain is the knob the operator turns
            # WHILE watching the loop settle, and a fresh PhaseCorrector would be a
            # behaviour change mid-run for a value they did not touch.
            self._corrector.target_phase = self._config.set_phase
            self._corrector.gain = self._config.loop_gain
            self._corrector.deadband = self._config.correction_deadband_rad
            self._corrector.invert = self._config.invert_correction
        self._notify(ConfigSynced(config=self._config))
        self._reply_ok(msg)
