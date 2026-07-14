from __future__ import annotations

import logging
from typing import Callable, TYPE_CHECKING

from base_core.ipc.threaded_worker import ThreadedWorker, worker_thread
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_tracker import PhaseTracker
from app_apps.analysis.phase_control.subprocess.domain.phase_corrector import PhaseCorrector
from app_apps.analysis.phase_control.subprocess.domain import fringe_fit
from app_apps.analysis.phase_control.subprocess.messages import (
    CorrectionAvailable,
    ConfigSynced,
    FitCurveAvailable,
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

    def _setup(self) -> None:
        self._unsubs.append(self._bus.subscribe(SetStabilizationConfig, self._on_set_config))
        self._unsubs.append(self._bus.subscribe(ProcessSpectrum, self._on_spectrum))

    def _start(self) -> None:
        self._tracker = PhaseTracker(self._config)
        self._corrector = PhaseCorrector()
        self._corrector.target_phase = self._config.set_phase
        self._paused = False
        log.info(
            "PhaseStabilizationWorker START: target_phase=%.3f deg, avg_spectra=%d, "
            "residuals_threshold=%s, fit_all_params=%s",
            self._config.set_phase.Deg, self._config.avg_spectra,
            self._config.residuals_threshold, self._config.fit_all_params,
        )

    def _pause(self) -> None:
        self._paused = True
        log.info("PhaseStabilizationWorker PAUSE (will ignore incoming spectra)")

    def _resume(self) -> None:
        self._paused = False
        log.info("PhaseStabilizationWorker RESUME")

    def _stop(self) -> None:
        self._tracker = PhaseTracker(self._config)
        self._corrector = PhaseCorrector()
        self._corrector.target_phase = self._config.set_phase
        log.info("PhaseStabilizationWorker STOP (tracker/corrector reset)")

    @worker_thread
    def _on_spectrum(self, msg: ProcessSpectrum) -> None:
        try:
            if self._paused or self._tracker is None or self._corrector is None:
                log.info(
                    "spectrum slot=%d: SKIP (paused=%s tracker=%s corrector=%s)",
                    msg.slot, self._paused,
                    self._tracker is not None, self._corrector is not None,
                )
                return
            buf = self._get_buffer()
            wl = buf.wavelengths(msg.slot)
            ins = buf.intensities(msg.slot)
            config_changed = self._tracker.update(wl, ins)
            if config_changed:
                self._notify(ConfigSynced(config=self._config))
                self._notify_fit_curve()
            phase = self._tracker.current_phase
            log.info(
                "spectrum slot=%d: phase_updated=%s, current_phase=%s "
                "(fit outcome logged by PhaseTracker above)",
                msg.slot, config_changed,
                f"{phase.Deg:.3f} deg" if phase is not None else "None (no successful fit yet)",
            )
            if phase is not None:
                result = self._corrector.update(phase)
                if result is not None:
                    log.info(
                        "spectrum slot=%d: CORRECTION -> notifying main: angle=%.4f deg sign=%+d",
                        msg.slot, result.angle.Deg, result.sign,
                    )
                    self._notify(CorrectionAvailable(angle=result.angle, sign=result.sign))
                else:
                    log.info(
                        "spectrum slot=%d: no correction emitted (within tolerance or phase==0) — "
                        "see PhaseCorrector logs for the exact reason",
                        msg.slot,
                    )
        except Exception:
            log.exception("PhaseStabilizationWorker: error processing spectrum slot %d", msg.slot)
        finally:
            self._notify(SpectrumProcessed(slot=msg.slot, item_id=msg.item_id, consumer_id=CONSUMER_ID))

    def _notify_fit_curve(self) -> None:
        """Publish the components of the latest good fit for the chart overlay."""
        res = self._tracker.last_result if self._tracker is not None else None
        if res is None:
            return
        try:
            wl, baseline, amplitude, phase = fringe_fit.display_curve(res)
        except Exception:
            log.exception("failed to build fit-curve overlay payload")
            return
        self._notify(FitCurveAvailable(
            wavelengths_nm=wl, baseline=baseline, amplitude=amplitude, phase=phase,
            phase_ref_rad=float(res["phase_ref"]),
        ))

    @worker_thread
    def _on_set_config(self, msg: SetStabilizationConfig) -> None:
        self._config = msg.config
        if self._tracker is not None:
            self._tracker = PhaseTracker(self._config)
        if self._corrector is not None:
            self._corrector.target_phase = self._config.set_phase
        self._notify(ConfigSynced(config=self._config))
        self._reply_ok(msg)
