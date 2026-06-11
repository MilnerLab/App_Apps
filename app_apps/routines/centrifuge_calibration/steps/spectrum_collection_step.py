from __future__ import annotations

from typing import Callable

import numpy as np

from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.routines.step import Step
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer

from app_apps.io.spectrometer.events import SpectrumAvailable, SpectrumAck


class SpectrumCollectionStep(Step):
    """
    Collects N spectra from the spectrometer buffer then fires on_ready.

    Reads each slot from shared memory and acks it so the coordinator can
    recycle the slot.  For the ack to participate in slot lifecycle the
    routine must be registered as a consumer with the SharedBufferCoordinator
    before start() is called.  # TODO: wire consumer registration in module
    """

    CONSUMER_ID = "centrifuge_calibration"

    def __init__(
        self,
        bus: EventBus,
        io: TaskRunner,
        spectrum_buffer: SharedSpectrumBuffer,
        *,
        n_spectra: int,
        on_ready: Callable[[list[np.ndarray]], None],
    ) -> None:
        super().__init__(bus, io)
        self._buffer = spectrum_buffer
        self._n = n_spectra
        self._on_ready = on_ready
        self._collected: list[np.ndarray] = []
        self._unsub: Callable[[], None] | None = None

    def start(self) -> None:
        self._collected.clear()
        self._unsub = self._bus.subscribe(SpectrumAvailable, self._on_spectrum_available)

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._collected.clear()

    def _on_spectrum_available(self, event: SpectrumAvailable) -> None:
        _header, spectrum = self._buffer.read_frame_copy(event.slot)
        self._bus.publish(SpectrumAck(slot=event.slot, item_id=event.item_id, consumer_id=self.CONSUMER_ID))

        self._collected.append(spectrum.copy())
        if len(self._collected) < self._n:
            return

        spectra = self._collected.copy()
        self._collected.clear()

        # Unsubscribe before firing callback so stop() is idempotent if the
        # callback triggers a transition that calls stop().
        unsub, self._unsub = self._unsub, None
        if unsub:
            unsub()

        self._on_ready(spectra)
