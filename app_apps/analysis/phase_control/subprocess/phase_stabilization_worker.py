from __future__ import annotations

import logging
import time
from typing import Callable, TYPE_CHECKING

from base_core.ipc.threaded_worker import ThreadedWorker, worker_thread
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_tracker import PhaseTracker
from app_apps.analysis.phase_control.subprocess.domain.phase_corrector import PhaseCorrector
from app_apps.analysis.phase_control.subprocess.messages import (
    CorrectionAvailable,
    ConfigSynced,
    ProcessSpectrum,
    SpectrumProcessed,
    SetStabilizationConfig,
    StabilizationAutoPaused,
)

if TYPE_CHECKING:
    from base_core.framework.events.event_bus import EventBus
    from base_core.ipc.subprocess_connector import SubprocessPipelineConnector
    from spm_002.buffer import SpectrumBuffer

log = logging.getLogger(__name__)

WORKER_ID = "phase_tracking"
CONSUMER_ID = "phase_tracking"

# Consecutive failed fits before the loop stops driving the plate (fitting continues, so the
# overlay stays live for the operator to drag against). At ~2-4 fps this is ~1-2 s of solid
# failure -- long enough not to trip on a single bad frame, short enough that a real clip
# stops corrections before the integrator walks the stage far.
AUTOPAUSE_FAILS = 5
# While auto-paused the fit is skipped so the raw stream runs smoothly; this is how often a
# single probe fit is still attempted, to notice the clip has been dragged out (or the beam
# recovered) and resume. 1 s is imperceptible on the stream and recovers within a frame or
# two of the fix. An operator drag resumes instantly regardless (see _on_set_config).
AUTOPAUSE_RETRY_S = 1.0


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
        self._tracker: PhaseTracker | None = None
        self._corrector: PhaseCorrector | None = None
        self._paused = True
        # Auto-pause state: after AUTOPAUSE_FAILS consecutive failures the loop stops both
        # correcting AND fitting (fitting resumes as a slow probe), distinct from _paused
        # (the operator's explicit pause).
        self._consec_fail = 0
        self._autopaused = False
        self._last_probe_t = 0.0      # last auto-pause probe-fit time (perf_counter)
        self._latest_item_id = -1     # newest arrival (drop-stale coalescing)
        self._skipped_since_fit = 0   # frames coalesced away since the last real fit

    def _setup(self) -> None:
        self._unsubs.append(self._bus.subscribe(SetStabilizationConfig, self._on_set_config))
        self._unsubs.append(self._bus.subscribe(ProcessSpectrum, self._on_spectrum))

    def _start(self) -> None:
        self._tracker = PhaseTracker(self._config)
        self._corrector = PhaseCorrector()
        self._corrector.target_phase = self._config.set_phase
        self._corrector.gain = self._config.loop_gain
        self._latest_item_id = -1
        self._skipped_since_fit = 0
        self._consec_fail = 0
        self._autopaused = False
        self._paused = False

    def _pause(self) -> None:
        self._paused = True

    def _resume(self) -> None:
        self._paused = False

    def _stop(self) -> None:
        self._tracker = PhaseTracker(self._config)
        self._corrector = PhaseCorrector()
        self._corrector.target_phase = self._config.set_phase
        self._corrector.gain = self._config.loop_gain
        self._latest_item_id = -1
        self._skipped_since_fit = 0

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
        try:
            if self._tracker is None or self._corrector is None:
                return
            if self._paused:
                return
            # Drop-stale: if a newer spectrum arrived while this one queued, skip
            # the fit (still acked below) so we only ever fit the freshest frame.
            if msg.item_id != self._latest_item_id:
                self._skipped_since_fit += 1
                return
            # While auto-paused, do NOT fit every frame: the fit is the heavy step
            # (~180-400 ms) and running it on a clip it cannot solve just starves the raw
            # spectrum stream the operator is trying to read while dragging the markers.
            # Instead PROBE once every AUTOPAUSE_RETRY_S -- enough to notice the operator
            # (or the beam) has fixed the clip and auto-resume, cheap enough to leave the
            # stream smooth. A config change (a drag) resumes immediately via _on_set_config.
            if self._autopaused:
                now = time.perf_counter()
                if now - self._last_probe_t < AUTOPAUSE_RETRY_S:
                    self._skipped_since_fit += 1
                    return
                self._last_probe_t = now
            buf = self._get_buffer()
            wl = buf.wavelengths(msg.slot)
            ins = buf.intensities(msg.slot)
            skipped = self._skipped_since_fit
            self._skipped_since_fit = 0
            committed = self._tracker.update(wl, ins, skipped=skipped)
            if committed:
                # A good frame clears the failure streak and lifts an auto-pause: the
                # operator's drag (or the beam) fixed it, and corrections resume.
                self._consec_fail = 0
                if self._autopaused:
                    self._autopaused = False
                    log.warning("AUTORESUME: a fit committed; corrections re-enabled")
                    self._notify(StabilizationAutoPaused(paused=False, consecutive_failures=0))
                self._notify(ConfigSynced(config=self._config))
                phase = self._tracker.current_phase
                if phase is not None:
                    result = self._corrector.update(phase)
                    if result is not None:
                        self._notify(CorrectionAvailable(angle=result.angle, sign=result.sign))
            else:
                # No commit. After enough consecutive failures, auto-pause: stop driving the
                # plate AND stop fitting every frame (the probe in _process_spectrum keeps a
                # slow retry going). A sustained failure means the data cannot support a phase
                # -- a clip the operator has not yet dragged out -- and both correcting on
                # nothing (winds the plate) and fitting flat out (starves the stream) are
                # wrong. The overlay holds its last good fit; the operator drags the geometry
                # and a probe fit -- or their drag -- auto-resumes.
                self._consec_fail += 1
                if not self._autopaused and self._consec_fail >= AUTOPAUSE_FAILS:
                    self._autopaused = True
                    self._last_probe_t = time.perf_counter()
                    log.warning("AUTOPAUSE: %d consecutive fits failed; corrections and "
                                "fitting held (slow probe only) until a fit commits -- drag "
                                "the clip edge / envelope centre", self._consec_fail)
                    self._notify(StabilizationAutoPaused(
                        paused=True, consecutive_failures=self._consec_fail))
        except Exception:
            log.exception("PhaseStabilizationWorker: error processing spectrum slot %d", msg.slot)
        finally:
            self._notify(SpectrumProcessed(slot=msg.slot, item_id=msg.item_id, consumer_id=CONSUMER_ID))

    @worker_thread
    def _on_set_config(self, msg: SetStabilizationConfig) -> None:
        self._config = msg.config
        # A new config is the operator acting -- typically dragging the clip edge or envelope
        # centre precisely to break an auto-pause. Clear the pause and the failure streak so
        # the very next frame is fit at full rate, instead of waiting out a probe interval.
        if self._autopaused:
            self._autopaused = False
            log.warning("AUTORESUME: config changed; fitting resumed at full rate")
            self._notify(StabilizationAutoPaused(paused=False, consecutive_failures=0))
        self._consec_fail = 0
        if self._tracker is not None:
            self._tracker = PhaseTracker(self._config)
        if self._corrector is not None:
            # Retuned in place, not reconstructed: gain is the knob the operator turns
            # WHILE watching the loop settle, and a fresh PhaseCorrector would be a
            # behaviour change mid-run for a value they did not touch.
            self._corrector.target_phase = self._config.set_phase
            self._corrector.gain = self._config.loop_gain
        self._notify(ConfigSynced(config=self._config))
        self._reply_ok(msg)
