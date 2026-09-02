from __future__ import annotations

import logging
import math
import time
from typing import Callable, TYPE_CHECKING

import numpy as np

from base_core.ipc.threaded_worker import ThreadedWorker, worker_thread
from base_core.math.models import Angle
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_corrector import (
    PhaseBatch,
    PhaseCorrector,
)
from app_apps.analysis.phase_control.subprocess.domain.stabilization_tracker import (
    StabilizationTracker,
    TrackerState,
)
from app_apps.analysis.phase_control.subprocess.messages import (
    BatchProgress,
    CaptureTarget,
    CorrectionAvailable,
    ConfigSynced,
    DropBatch,
    ProcessSpectrum,
    SetStabilizationConfig,
    SpectrumProcessed,
)

_TWO_PI = 2.0 * math.pi

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
        self._tracker: StabilizationTracker | None = None
        self._corrector: PhaseCorrector | None = None
        # The loop's whole state: one non-overlapping block of accepted phases, and a
        # settle deadline covering the rotation the last correction commanded. Capture is
        # the TRACKER's state, not the loop's -- the loop simply gets no phases while it runs.
        self._batch = PhaseBatch(config.avg_spectra)
        self._settle_until = 0.0
        self._paused = True
        self._latest_item_id = -1     # newest arrival (drop-stale coalescing)
        self._skipped_since_fit = 0   # frames coalesced away since the last real fit
        # --- throughput diagnostics (periodic THROUGHPUT log) ---
        self._tp_t0 = time.perf_counter()
        self._tp_fit = 0              # frames actually fit in the window
        self._tp_skip = 0            # frames coalesced/dropped in the window
        self._tp_commit = 0          # fits that passed the gate in the window
        self._tp_fit_ms = 0.0        # summed fit wall time in the window
        # Last progress put on the wire, so the per-frame publish below can stay silent
        # while nothing moves. None = nothing published yet.
        self._last_progress_stamp: tuple | None = None

    def _setup(self) -> None:
        self._unsubs.append(self._bus.subscribe(SetStabilizationConfig, self._on_set_config))
        self._unsubs.append(self._bus.subscribe(ProcessSpectrum, self._on_spectrum))
        self._unsubs.append(self._bus.subscribe(CaptureTarget, self._on_capture_target))
        self._unsubs.append(self._bus.subscribe(DropBatch, self._on_drop_batch))

    def _start(self) -> None:
        self._build_tracker()
        self._latest_item_id = -1
        self._skipped_since_fit = 0
        self._paused = False

    def _pause(self) -> None:
        self._paused = True
        # Discard the block: it describes frames the loop was not acting on, and resuming
        # onto a half-filled one would correct on a mean that straddles the pause.
        self._batch.clear()
        self._publish_progress()

    def _resume(self) -> None:
        self._paused = False

    def _stop(self) -> None:
        self._build_tracker()
        self._latest_item_id = -1
        self._skipped_since_fit = 0

    def _build_tracker(self) -> None:
        # Starts in CAPTURING: there is no frozen shape yet and so nothing to phase-track
        # against, which makes the cold reference run the only thing it CAN do on start.
        self._tracker = StabilizationTracker(self._config)
        self._corrector = PhaseCorrector()
        self._corrector.target_phase = self._config.set_phase
        self._corrector.tolerance = self._config.phase_tolerance
        self._corrector.invert = self._config.invert_correction
        self._batch = PhaseBatch(self._config.avg_spectra)
        self._settle_until = 0.0
        self._last_progress_stamp = None   # a fresh loop re-announces itself unconditionally
        self._publish_progress()

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
            if outcome.target_phase is not None:
                # A capture completed: the shape is frozen and this is the phase it measures,
                # so adopting it as the setpoint leaves the loop at exactly zero error.
                self._adopt_target(outcome.target_phase)
            if outcome.phase_abs is not None:
                self._redraw_at(outcome.phase_abs, outcome.delta)
                self._collect(outcome.phase_abs, now)
            self._publish_progress()
        except Exception:
            log.exception("PhaseStabilizationWorker: error processing spectrum slot %d", msg.slot)
        finally:
            ack()

    # -------------------------------------------------------------------- block loop --
    def _collect(self, phase_rad: float, now: float) -> None:
        """Add one accepted phase to the block, and act when the block fills.

        Frames arriving inside the settle window are DROPPED, not queued: they were taken
        while the plate was still turning, so the phase they report belongs to neither the
        state before the move nor the one after. The legacy loop got this from the rotator's
        own is_busy flag; ``move_settle_s`` stands in for it across the process boundary.
        """
        if now < self._settle_until:
            return
        self._batch.add(phase_rad)
        if not self._batch.full:
            return

        n = self._batch.count
        coh = self._batch.coherence()
        mean = self._batch.take()
        if mean is None:
            # The block cancelled out -- its mean angle is undefined. It has already been
            # cleared, so the loop simply collects a fresh one rather than acting on it.
            log.info("block: %d frames cancelled (coherence %.2f), no correction", n, coh)
            return

        self._emit_correction(Angle(mean % _TWO_PI), n, coh)

    def _redraw_at(self, phase_abs: float, delta: float) -> None:
        """Move the committed params onto the phase this frame measured.

        Only the constant term moves. ``delta`` is measured relative to the frozen
        polynomial, so setting c0 to (frozen c0 + delta) makes the polynomial evaluate to the
        measured phase at every wavelength while leaving the carrier and chirp exactly as
        captured -- which is the point of freezing them. The chart then shows the frozen
        shape sitting where the light actually is, and the target curve is the same shape at
        set_phase, so the gap between the two IS the error the loop is about to correct.
        """
        tpl = self._tracker.template if self._tracker is not None else None
        if tpl is None:
            return
        self._config.params.c0 = float(tpl.csig[0]) + float(delta)
        self._config.params.phase_ref = float(phase_abs)
        self._notify(ConfigSynced(config=self._config))

    def _adopt_target(self, phase_abs: float) -> None:
        """Take the freshly captured phase as the setpoint, and start the block over.

        The block is cleared because anything in it was measured against the OLD frozen
        shape: those phases are not comparable with the ones the new reference produces, and
        averaging across the two would define the first correction partly from a model that
        no longer exists.
        """
        self._batch.clear()
        self._settle_until = 0.0
        self._config.set_phase = Angle(phase_abs % _TWO_PI)
        if self._corrector is not None:
            self._corrector.target_phase = self._config.set_phase
        log.info("capture: set_phase = %.3f rad (phase re-zeroed)", phase_abs % _TWO_PI)
        self._notify(ConfigSynced(config=self._config))

    def _emit_correction(self, phase: Angle, frames: int, coherence: float) -> None:
        assert self._corrector is not None
        result = self._corrector.update(phase)
        if result is None:
            log.info("block: %d frames, phi=%.3f rad (coherence %.2f) -- inside deadband, holding",
                     frames, float(phase), coherence)
            return
        log.info("block: %d frames, phi=%.3f rad (coherence %.2f) -> rotate %.3f deg",
                 frames, float(phase), coherence, result.angle.Deg)
        self._notify(CorrectionAvailable(angle=result.angle, sign=result.sign))
        # Hold off collecting until the plate has actually moved. Set only on a real move:
        # a correction inside the deadband commands nothing, so there is nothing to settle.
        self._settle_until = time.perf_counter() + self._config.move_settle_s

    def _publish_progress(self) -> None:
        """Notify the UI of block progress, but only when it has changed.

        Called per frame from the fit path, so it has to be cheap and quiet: a loop sitting
        in its settle window must not put an IPC message on the wire at the frame rate.
        """
        if self._tracker is None:
            return
        settling = time.perf_counter() < self._settle_until
        capturing = self._tracker.state == TrackerState.CAPTURING
        # While capturing, the count that matters to the operator is the reference run, not
        # the averaging block -- the block is not filling at all. One pair of numbers, and
        # `capturing` says which of the two things they are counting.
        got, need = (self._tracker.capture_progress if capturing
                     else (self._batch.count, self._batch.size))
        stamp = (got, need, capturing, settling)
        if stamp == self._last_progress_stamp:
            return
        self._last_progress_stamp = stamp
        self._notify(BatchProgress(
            collected=got, needed=need,
            coherence=self._batch.coherence(), capturing=capturing,
            settling=settling, error_deg=self._running_error_deg(),
        ))

    def _running_error_deg(self) -> float:
        """How far the block currently sits from the setpoint, in degrees.

        Folded through the corrector's own wrap so the number on the panel is the same one
        the deadband is tested against -- including the fold at pi. Publishing a differently
        wrapped error would make the loop look like it was ignoring a large error whenever
        the two disagreed.

        The block is not consumed: this rides along on a progress publish, which only
        happens when the count changed, so it costs nothing extra on the wire.
        """
        mean = self._batch.mean_now()
        if mean is None or self._corrector is None:
            return float("nan")
        err = self._corrector.wrap_error(Angle(mean % _TWO_PI))
        return float(err.Deg)

    @worker_thread
    def _on_capture_target(self, msg: CaptureTarget) -> None:
        if self._tracker is not None:
            self._tracker.request_capture()
        # The collected phases were measured against the shape that is about to be replaced.
        self._batch.clear()
        self._settle_until = 0.0
        self._publish_progress()
        self._reply_ok(msg)

    def _on_drop_batch(self, msg: DropBatch) -> None:
        # Runs on the poll thread and touches only the block -- deliberately NOT dispatched
        # onto the worker thread. A commanded move must discard the block BEFORE the
        # disturbed spectra are fit, and the worker thread may be mid-fit for hundreds of ms.
        if self._batch.count:
            log.info("block: dropped %d frames (%s)", self._batch.count, msg.reason)
        self._batch.clear()
        self._publish_progress()

    @worker_thread
    def _on_set_config(self, msg: SetStabilizationConfig) -> None:
        self._config = msg.config
        # A config change can move lambda_ref, the window or the target, so the phases
        # already collected no longer describe the same quantity. The block goes; there is
        # nothing else to preserve, which is the point of a loop with no frozen state.
        self._batch.clear()
        self._batch.resize(self._config.avg_spectra)
        # A config edit does NOT drop the frozen reference. The shape did not change
        # because a threshold was nudged, and re-capturing on every edit is exactly the
        # spurious refit that made the previous loop untrustworthy.
        if self._tracker is not None:
            self._tracker.retune(self._config)
        if self._corrector is not None:
            # Retuned in place, not reconstructed: these are knobs the operator turns WHILE
            # watching the loop, and a fresh PhaseCorrector would be a behaviour change
            # mid-run for values they did not touch.
            self._corrector.target_phase = self._config.set_phase
            self._corrector.tolerance = self._config.phase_tolerance
            self._corrector.invert = self._config.invert_correction
        self._publish_progress()
        self._notify(ConfigSynced(config=self._config))
        self._reply_ok(msg)
