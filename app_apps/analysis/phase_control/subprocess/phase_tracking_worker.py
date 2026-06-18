from __future__ import annotations

import logging
from typing import Callable, TYPE_CHECKING

from base_core.ipc.worker import BaseWorker
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_tracker import PhaseTracker
from app_apps.analysis.phase_control.subprocess.domain.phase_corrector import PhaseCorrector
from app_apps.analysis.phase_control.subprocess.messages import (
    CorrectionAvailable,
    ConfigSynced,
    ProcessSpectrum,
    SpectrumProcessed,
    SetPaused,
    SetStabilizationConfig,
)

if TYPE_CHECKING:
    from base_core.framework.events.event_bus import EventBus
    from base_core.ipc.subprocess_connector import SubprocessPipelineConnector
    from spm_002.buffer import SpectrumBuffer

log = logging.getLogger(__name__)

WORKER_ID = "phase_tracking"
CONSUMER_ID = "phase_tracking"


class PhaseTrackingWorker(BaseWorker):
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
        self._unsubs.append(self._bus.subscribe(SetPaused, self._on_set_paused))
        self._unsubs.append(self._bus.subscribe(ProcessSpectrum, self._on_spectrum))

    def _start(self) -> None:
        self._tracker = PhaseTracker(self._config)
        self._corrector = PhaseCorrector()
        self._paused = False

    def _stop(self) -> None:
        self._tracker = None
        self._corrector = None
        self._paused = True

    def _reset(self) -> None:
        self._tracker = PhaseTracker(self._config)
        self._corrector = PhaseCorrector()

    def _on_spectrum(self, msg: ProcessSpectrum) -> None:
        try:
            if self._paused or self._tracker is None or self._corrector is None:
                return
            buf = self._get_buffer()
            wl = buf.wavelengths(msg.slot)
            ins = buf.intensities(msg.slot)
            config_changed = self._tracker.update(wl, ins)
            if config_changed:
                self._notify(ConfigSynced(config=self._config))
            phase = self._tracker.current_phase
            if phase is not None:
                result = self._corrector.update(phase)
                if result is not None:
                    self._notify(CorrectionAvailable(angle=result.angle, sign=result.sign))
        except Exception:
            log.exception("PhaseTrackingWorker: error processing spectrum slot %d", msg.slot)
        finally:
            self._notify(SpectrumProcessed(slot=msg.slot, item_id=msg.item_id, consumer_id=CONSUMER_ID))

    def _on_set_config(self, msg: SetStabilizationConfig) -> None:
        self._config = msg.config
        if self._tracker is not None:
            self._tracker = PhaseTracker(self._config)
        self._notify(ConfigSynced(config=self._config))
        self._reply_ok(msg)

    def _on_set_paused(self, msg: SetPaused) -> None:
        if msg.worker_id != self._worker_id:
            return
        self._paused = msg.paused
        self._reply_ok(msg)
