from __future__ import annotations

from typing import Callable

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.service_connector import ServicePipelineConnector
from base_core.ipc.worker_handle import BaseWorkerHandle
from app_apps.analysis.phase_control.events import (
    PhaseBatchChanged,
    PhaseTrackingStateChanged,
    StabilizationConfigChanged,
)

from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.messages import (
    BatchProgress,
    CaptureTarget,
    ConfigSynced,
    CorrectionAvailable,
    DropBatch,
    SetStabilizationConfig,
    SpectrumProcessed,
)
from app_apps.io.control_readout.mfa_cc.events import RequestMoveMfacc
from app_apps.io.control_readout.rgv.events import RequestRotateRGV
from app_apps.io.control_readout.uts150cc.events import RequestMoveUts150cc
from app_apps.io.spectrometer.events import SpectrumAck
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle


class PhaseStabilizationHandle(BaseWorkerHandle):
    WORKER_ID = "phase_tracking"
    CONSUMER_ID = "phase_tracking"

    def __init__(self, bus: EventBus, spectrum_writer: SpectrometerWorkerHandle, config: StabilizationConfig) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=PhaseTrackingStateChanged)
        self._spectrum_writer = spectrum_writer
        self._config = config
        self._unsub_config_synced: Callable[[], None] | None = None

    def subscribe(self) -> None:
        self._subscribe_service(CorrectionAvailable, self._on_correction_available)
        self._subscribe_service(SpectrumProcessed, self._on_spectrum_processed)
        self._subscribe_service(BatchProgress, self._on_batch_progress)
        # Command-driven block drop, with zero lag: a commanded delay (MFA-CC) or grating
        # (UTS150CC) move disturbs the fringes, so the partially collected block goes the
        # moment the move is REQUESTED, before the disturbed spectra can be fit. The frozen
        # reference is NOT dropped -- re-referencing is the operator's call, and a move that
        # changes the shape enough to need one is a move they know they made.
        #
        # The probe stage (FMS300PP) is deliberately absent: it does not change the shape.
        self._subscribe(RequestMoveMfacc, self._on_delay_move)
        self._subscribe(RequestMoveUts150cc, self._on_grating_move)
        self._spectrum_writer.register_consumer(self.CONSUMER_ID)

    def unsubscribe(self) -> None:
        super().unsubscribe()
        self._spectrum_writer.unregister_consumer(self.CONSUMER_ID)

    def _bind(self, connector: ServicePipelineConnector, service_bus: EventBus) -> None:
        # ConfigSynced must stay handled independent of Start/Pause/Resume/Stop (unlike
        # subscribe()/unsubscribe() above), so the shared StabilizationConfig — and anything
        # reacting to StabilizationConfigChanged, e.g. the chart overlay curves — stays correct
        # even before the worker has ever been started, or while it's paused.
        super()._bind(connector, service_bus)
        self._unsub_config_synced = service_bus.subscribe(ConfigSynced, self._on_config_synced)

    def _unbind(self) -> None:
        if self._unsub_config_synced is not None:
            self._unsub_config_synced()
            self._unsub_config_synced = None
        super()._unbind()

    def _on_disconnect(self) -> None:
        if self._unsub_config_synced is not None:
            self._unsub_config_synced()
            self._unsub_config_synced = None
        super()._on_disconnect()

    def _on_correction_available(self, msg: CorrectionAvailable) -> None:
        self._bus.publish(RequestRotateRGV(angle=msg.angle))

    def _on_spectrum_processed(self, msg: SpectrumProcessed) -> None:
        self._bus.publish(SpectrumAck(slot=msg.slot, item_id=msg.item_id, consumer_id=msg.consumer_id))

    def _on_config_synced(self, msg: ConfigSynced) -> None:
        self._config.copy_from(msg.config)
        self._bus.publish(StabilizationConfigChanged())

    @property
    def config(self) -> StabilizationConfig:
        return self._config

    def set_config(self, config: StabilizationConfig) -> None:
        self._request(SetStabilizationConfig(config=config), self._on_set_config_reply)

    def _on_set_config_reply(self, reply: OKReply) -> None:
        pass

    # --------------------------------------------------------------------- block loop --
    def capture_target(self) -> None:
        self._request(CaptureTarget(), self._on_set_config_reply)

    def _on_delay_move(self, event: RequestMoveMfacc) -> None:
        self._emit(DropBatch(reason="delay move"))

    def _on_grating_move(self, event: RequestMoveUts150cc) -> None:
        self._emit(DropBatch(reason="grating move"))

    def _on_batch_progress(self, msg: BatchProgress) -> None:
        self._bus.publish(PhaseBatchChanged(
            collected=msg.collected, needed=msg.needed, coherence=msg.coherence,
            capturing=msg.capturing, settling=msg.settling,
            error_deg=msg.error_deg,
        ))
