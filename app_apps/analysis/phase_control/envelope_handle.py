from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from app_apps.analysis.phase_control.subprocess.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.subprocess.messages import SetEnvelopeConfig, SetPaused, SpectrumProcessed
from app_apps.io.spectrometer.events import SpectrumAck
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle


class EnvelopeHandle(BaseWorkerHandle):
    WORKER_ID = "envelope"
    CONSUMER_ID = "envelope"

    def __init__(self, bus: EventBus, spectrum_writer: SpectrometerWorkerHandle) -> None:
        super().__init__(self.WORKER_ID, bus)
        self._spectrum_writer = spectrum_writer

    def subscribe(self) -> None:
        self._subscribe_service(SpectrumProcessed, self._on_spectrum_processed)
        # Envelope starts paused — do not register as consumer until explicitly unpaused.

    def _unbind(self) -> None:
        self._spectrum_writer.unregister_consumer(self.CONSUMER_ID)
        super()._unbind()

    def _on_spectrum_processed(self, msg: SpectrumProcessed) -> None:
        self._bus.publish(SpectrumAck(slot=msg.slot, item_id=msg.item_id, consumer_id=msg.consumer_id))

    def set_config(self, config: EnvelopeConfig) -> None:
        self._request(SetEnvelopeConfig(config=config), self._on_set_config_reply)

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._spectrum_writer.unregister_consumer(self.CONSUMER_ID)
        else:
            self._spectrum_writer.register_consumer(self.CONSUMER_ID)
        self._request(SetPaused(worker_id=self.WORKER_ID, paused=paused), self._on_set_paused_reply)

    def _on_set_config_reply(self, reply: OKReply) -> None:
        pass

    def _on_set_paused_reply(self, reply: OKReply) -> None:
        pass
