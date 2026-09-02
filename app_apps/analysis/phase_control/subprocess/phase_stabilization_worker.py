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
        self._paused = True
        self._latest_item_id = -1     # newest arrival (drop-stale coalescing)
        self._skipped_since_fit = 0   # frames coalesced away since the last real fit
        # --- throughput diagnostics (periodic THROUGHPUT log) ---
        self._tp_t0 = time.perf_counter()
        self._tp_fit = 0              # frames actually fit in the window
        self._tp_skip = 0            # frames coalesced/dropped in the window
        self._tp_commit = 0          # fits that passed the gate in the window
        self._tp_fit_ms = 0.0        # summed fit wall time in the window

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
        # Arm the capture immediately in slow mode, rather than waiting to be asked. The
        # tracker starts OFF, which is the cold per-frame loop -- so without this, starting
        # stabilization silently gave the operator the fast loop while the panel offered no
        # hint that a button press stood between them and the one they had selected.
        # Capture holds (issues no correction) for ~5 s at 2 Hz, then locks.
        if self._config.slow_correction and self._tracker is not None:
            self._tracker.request_capture()
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
        if now - self._last_correction < self._config.correction_period_s:
            return
        self._last_correction = now
        mean = self._averager.value()
        if mean is None:
            return
        n = self._averager.count
        self._averager.reset()
        log.info("template: correcting on the mean of %d frames, phi=%.3f rad", n, mean)
        self._emit_correction(Angle(mean % _TWO_PI), _TEMPLATE_CORRECTION_GAIN)

    def _emit_correction(self, phase: Angle, gain: float) -> None:
        assert self._corrector is not None
        # Set per call rather than at construction: the two modes correct at different
        # cadences and so need different gains, and the corrector is the one place the gain
        # is clamped before it can reach the stage.
        self._corrector.gain = gain
        result = self._corrector.update(phase)
        if result is not None:
            self._notify(CorrectionAvailable(angle=result.angle, sign=result.sign))

    def _publish_template_state(self) -> None:
        if self._tracker is None:
            return
        got, need = self._tracker.capture_progress
        self._notify(TemplateStateChanged(
            state=self._tracker.state.value, captured=got, needed=need,
            template=self._tracker.template,
        ))

    @worker_thread
    def _on_capture_reference(self, msg: CaptureReference) -> None:
        if self._tracker is not None:
            self._tracker.request_capture()
            self._averager.reset()
            self._publish_template_state()
        self._reply_ok(msg)

    @worker_thread
    def _on_recall_reference(self, msg: RecallReference) -> None:
        if self._tracker is not None and msg.template is not None:
            self._tracker.install(msg.template)
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
                    self._tracker.request_capture()
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
            self._corrector.invert = self._config.invert_correction
        self._notify(ConfigSynced(config=self._config))
        self._reply_ok(msg)
