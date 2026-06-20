from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from app_apps.analysis.phase_control.events import StabilizationConfigChanged
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.messages import (
    ConfigSynced,
    CorrectionAvailable,
    SetPaused,
    SetStabilizationConfig,
    SpectrumProcessed,
)
from app_apps.io.control_readout.ell14.events import RequestRotate
from app_apps.io.spectrometer.events import SpectrumAck
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle


class PhaseTrackingHandle(BaseWorkerHandle):
    WORKER_ID = "phase_tracking"
    CONSUMER_ID = "phase_tracking"

    def __init__(self, bus: EventBus, spectrum_writer: SpectrometerWorkerHandle, config: StabilizationConfig) -> None:
        super().__init__(self.WORKER_ID, bus)
        self._spectrum_writer = spectrum_writer
        self._config = config

    def subscribe(self) -> None:
        self._subscribe_service(CorrectionAvailable, self._on_correction_available)
        self._subscribe_service(SpectrumProcessed, self._on_spectrum_processed)
        self._subscribe_service(ConfigSynced, self._on_config_synced)
        self._spectrum_writer.register_consumer(self.CONSUMER_ID)

    def _unbind(self) -> None:
        self._spectrum_writer.unregister_consumer(self.CONSUMER_ID)
        super()._unbind()

    def _on_correction_available(self, msg: CorrectionAvailable) -> None:
        self._bus.publish(RequestRotate(angle=msg.angle, sign=msg.sign))

    def _on_spectrum_processed(self, msg: SpectrumProcessed) -> None:
        self._bus.publish(SpectrumAck(slot=msg.slot, item_id=msg.item_id, consumer_id=msg.consumer_id))

    def _on_config_synced(self, msg: ConfigSynced) -> None:
        self._config.copy_from(msg.config)
        self._bus.publish(StabilizationConfigChanged())

    def set_config(self, config: StabilizationConfig) -> None:
        self._request(SetStabilizationConfig(config=config), self._on_set_config_reply)

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
