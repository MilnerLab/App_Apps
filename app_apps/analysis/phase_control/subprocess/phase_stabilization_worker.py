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

# Consecutive failed fits before the loop stops driving the plate (~1-2 s at 2-4 fps). See
# docs for the auto-pause behaviour.
AUTOPAUSE_FAILS = 5
# While auto-paused, attempt a single probe fit only this often, so the raw stream stays smooth
# while still noticing a recovery. An operator config change resumes immediately regardless.
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
            # While auto-paused, probe only once every AUTOPAUSE_RETRY_S instead of fitting
            # every frame (the fit is heavy, ~180-400 ms), so the raw stream stays readable
            # while the operator drags the markers.
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
                # No commit. After AUTOPAUSE_FAILS in a row, auto-pause: stop driving the plate
                # and drop to a slow probe (see docs). The overlay holds its last good fit.
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
        # A new config means the operator is acting; clear any auto-pause and the failure
        # streak so the next frame is fit at full rate.
        if self._autopaused:
            self._autopaused = False
            log.warning("AUTORESUME: config changed; fitting resumed at full rate")
            self._notify(StabilizationAutoPaused(paused=False, consecutive_failures=0))
        self._consec_fail = 0
        if self._tracker is not None:
            self._tracker = PhaseTracker(self._config)
        if self._corrector is not None:
            # Retuned in place, not reconstructed: gain is tuned live while the loop settles.
            self._corrector.target_phase = self._config.set_phase
            self._corrector.gain = self._config.loop_gain
        self._notify(ConfigSynced(config=self._config))
        self._reply_ok(msg)
