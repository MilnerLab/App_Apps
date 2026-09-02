from __future__ import annotations

from typing import Callable

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.service_connector import ServicePipelineConnector
from base_core.ipc.worker_handle import BaseWorkerHandle
from base_core.quantities.enums import Prefix
from app_apps.analysis.phase_control.events import (
    PhaseCorrectionReported,
    PhaseMeanReported,
    PhaseTemplateChanged,
    PhaseTrackingStateChanged,
    StabilizationConfigChanged,
)

from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_template import PhaseTemplate
from app_apps.analysis.phase_control.subprocess.messages import (
    CaptureReference,
    ConfigSynced,
    CorrectionAvailable,
    CorrectionStatus,
    InvalidateTemplate,
    RecallReference,
    RunningPhaseMean,
    SetStabilizationConfig,
    SpectrumProcessed,
    TemplateStateChanged,
)
from app_apps.io.control_readout.rgv.events import RequestRotateRGV
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
        # The last template the worker installed. Kept here so Save reference has something
        # to write without a round trip to the subprocess.
        self._template: PhaseTemplate | None = None

    def subscribe(self) -> None:
        self._subscribe_service(CorrectionAvailable, self._on_correction_available)
        self._subscribe_service(CorrectionStatus, self._on_correction_status)
        self._subscribe_service(RunningPhaseMean, self._on_running_mean)
        self._subscribe_service(SpectrumProcessed, self._on_spectrum_processed)
        self._subscribe_service(TemplateStateChanged, self._on_template_state)
        # NO automatic template invalidation. A commanded delay (MFA-CC) or grating
        # (UTS150CC) move does change the fringe shape, and this used to drop the template
        # the moment such a move was requested -- which meant the reference was thrown away
        # at every setpoint of every scan, i.e. during exactly the runs it exists to hold
        # together. The reference is now installed and dropped by the operator only;
        # InvalidateTemplate is still handled by the worker for a routine that wants to
        # command it explicitly.
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

    def _on_running_mean(self, msg: RunningPhaseMean) -> None:
        # Straight through: the panel draws the averaged trace from this.
        self._bus.publish(PhaseMeanReported(
            mean_phase_rad=msg.mean_phase_rad, frames=msg.frames,
            coherence=msg.coherence, valid=msg.frames > 0,
        ))

    def _on_correction_status(self, msg: CorrectionStatus) -> None:
        # Straight through to the bus: this is a readout, and the panel is the only consumer.
        self._bus.publish(PhaseCorrectionReported(
            phase_error_rad=msg.phase_error_rad, commanded_deg=msg.commanded_deg,
            applied=msg.applied, reason=msg.reason, period_s=msg.period_s,
            frames=msg.frames,
        ))

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

    # ------------------------------------------------------------------ frozen template --
    @property
    def template(self) -> PhaseTemplate | None:
        """The installed template, or None. Save reference writes this out."""
        return self._template

    def capture_reference(self) -> None:
        self._request(CaptureReference(), self._on_set_config_reply)

    def recall_reference(self, template: PhaseTemplate | None) -> None:
        """Install a pinned template, or ``None`` to deselect the recalled one."""
        self._request(RecallReference(template=template), self._on_set_config_reply)

    def invalidate_template(self, reason: str) -> None:
        """Drop the installed reference. For a routine that knows the shape has changed --
        nothing calls this automatically."""
        self._emit(InvalidateTemplate(reason=reason))

    def _on_template_state(self, msg: TemplateStateChanged) -> None:
        self._template = msg.template
        if msg.template is not None:
            # Stamp the provenance the subprocess cannot know: the spectrometer settings live
            # on this side. A recalled template can then be checked against the machine it is
            # loaded onto, since a different integration time or averaging count changes the
            # noise the template was fitted through -- silently.
            cfg = self._spectrum_writer.config
            msg.template.integration_ms = float(cfg.exposure_time.value(Prefix.MILLI))
            msg.template.averages = int(cfg.average)
        self._bus.publish(PhaseTemplateChanged(
            state=msg.state, captured=msg.captured, needed=msg.needed, template=msg.template,
            pinned=msg.pinned, abandoned=msg.abandoned,
        ))
