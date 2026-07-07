from __future__ import annotations

import logging
from typing import Callable, TYPE_CHECKING

from base_core.ipc.threaded_worker import ThreadedWorker, worker_thread
from app_apps.analysis.phase_control.subprocess.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.subprocess.domain.envelope_optimizer import EnvelopeOptimizer
from app_apps.analysis.phase_control.subprocess.messages import (
    CorrectionAvailable,
    ProcessSpectrum,
    SpectrumProcessed,
    SetEnvelopeConfig,
)

if TYPE_CHECKING:
    from base_core.framework.events.event_bus import EventBus
    from base_core.ipc.subprocess_connector import SubprocessPipelineConnector
    from spm_002.buffer import SpectrumBuffer

log = logging.getLogger(__name__)

WORKER_ID = "envelope"
CONSUMER_ID = "envelope"


class EnvelopeWorker(ThreadedWorker):
    def __init__(
        self,
        bus: EventBus,
        connector: SubprocessPipelineConnector,
        config: EnvelopeConfig,
        get_buffer: Callable[[], SpectrumBuffer],
    ) -> None:
        super().__init__(WORKER_ID, bus, connector)
        self._config = config
        self._get_buffer = get_buffer
        self._optimizer: EnvelopeOptimizer | None = None
        self._paused = True

    def _setup(self) -> None:
        self._unsubs.append(self._bus.subscribe(SetEnvelopeConfig, self._on_set_config))
        self._unsubs.append(self._bus.subscribe(ProcessSpectrum, self._on_spectrum))

    def _start(self) -> None:
        self._optimizer = EnvelopeOptimizer(self._config)
        self._paused = False

    def _pause(self) -> None:
        self._paused = True

    def _resume(self) -> None:
        self._paused = False

    def _stop(self) -> None:
        if self._optimizer is not None:
            self._optimizer.reset()

    @worker_thread
    def _on_spectrum(self, msg: ProcessSpectrum) -> None:
        try:
            if self._paused or self._optimizer is None:
                return
            buf = self._get_buffer()
            wl = buf.wavelengths(msg.slot)
            ins = buf.intensities(msg.slot)
            result = self._optimizer.update(wl, ins)
            if result is not None:
                self._notify(CorrectionAvailable(angle=result.angle, sign=result.sign))
        except Exception:
            log.exception("EnvelopeWorker: error processing spectrum slot %d", msg.slot)
        finally:
            self._notify(SpectrumProcessed(slot=msg.slot, item_id=msg.item_id, consumer_id=CONSUMER_ID))

    @worker_thread
    def _on_set_config(self, msg: SetEnvelopeConfig) -> None:
        self._config = msg.config
        self._optimizer = EnvelopeOptimizer(self._config)
        self._reply_ok(msg)
