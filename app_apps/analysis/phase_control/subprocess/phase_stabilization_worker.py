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
)

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
        self._tracker: PhaseTracker | None = None
        self._corrector: PhaseCorrector | None = None
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

    def _start(self) -> None:
        self._tracker = PhaseTracker(self._config)
        self._corrector = PhaseCorrector()
        self._corrector.target_phase = self._config.set_phase
        self._latest_item_id = -1
        self._skipped_since_fit = 0
        self._paused = False

    def _pause(self) -> None:
        self._paused = True

    def _resume(self) -> None:
        self._paused = False

    def _stop(self) -> None:
        self._tracker = PhaseTracker(self._config)
        self._corrector = PhaseCorrector()
        self._corrector.target_phase = self._config.set_phase
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
            if self._paused or self._tracker is None or self._corrector is None:
                return
            # Drop-stale: if a newer spectrum arrived while this one queued, skip
            # the fit (still acked below) so we only ever fit the freshest frame.
            if msg.item_id != self._latest_item_id:
                self._skipped_since_fit += 1
                self._tp_skip += 1
                return
            buf = self._get_buffer()
            wl = buf.wavelengths(msg.slot)
            ins = buf.intensities(msg.slot)
            skipped = self._skipped_since_fit
            self._skipped_since_fit = 0
            t_fit0 = time.perf_counter()
            committed = self._tracker.update(wl, ins, skipped=skipped)
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
                phase = self._tracker.current_phase
                if phase is not None:
                    result = self._corrector.update(phase)
                    if result is not None:
                        self._notify(CorrectionAvailable(angle=result.angle, sign=result.sign))
        except Exception:
            log.exception("PhaseStabilizationWorker: error processing spectrum slot %d", msg.slot)
        finally:
            self._notify(SpectrumProcessed(slot=msg.slot, item_id=msg.item_id, consumer_id=CONSUMER_ID))

    @worker_thread
    def _on_set_config(self, msg: SetStabilizationConfig) -> None:
        self._config = msg.config
        if self._tracker is not None:
            self._tracker = PhaseTracker(self._config)
        if self._corrector is not None:
            self._corrector.target_phase = self._config.set_phase
        self._notify(ConfigSynced(config=self._config))
        self._reply_ok(msg)
