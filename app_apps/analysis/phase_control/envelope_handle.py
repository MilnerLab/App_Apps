from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from app_apps.analysis.phase_control.events import EnvelopeStateChanged
from app_apps.analysis.phase_control.subprocess.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.subprocess.messages import SetEnvelopeConfig, SpectrumProcessed
from app_apps.io.spectrometer.events import SpectrumAck
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle


class EnvelopeHandle(BaseWorkerHandle):
    WORKER_ID = "envelope"
    CONSUMER_ID = "envelope"

    def __init__(self, bus: EventBus, spectrum_writer: SpectrometerWorkerHandle) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=EnvelopeStateChanged)
        self._spectrum_writer = spectrum_writer

    def subscribe(self) -> None:
        self._subscribe_service(SpectrumProcessed, self._on_spectrum_processed)
        self._spectrum_writer.register_consumer(self.CONSUMER_ID)

    def unsubscribe(self) -> None:
        super().unsubscribe()
        self._spectrum_writer.unregister_consumer(self.CONSUMER_ID)

    def _on_spectrum_processed(self, msg: SpectrumProcessed) -> None:
        self._bus.publish(SpectrumAck(slot=msg.slot, item_id=msg.item_id, consumer_id=msg.consumer_id))

    def set_config(self, config: EnvelopeConfig) -> None:
        self._request(SetEnvelopeConfig(config=config), self._on_set_config_reply)

    def _on_set_config_reply(self, reply: OKReply) -> None:
        pass
