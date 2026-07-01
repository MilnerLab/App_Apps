from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.subprocess_service import SubprocessService
from spm_002.buffer import SpectrumBuffer, SpectrumMemorySpec
from app_apps.analysis.phase_control.subprocess.domain.mode import ControlMode
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.subprocess.messages import ProcessSpectrum
from app_apps.io.spectrometer.events import SpectrumAvailable

if TYPE_CHECKING:
    from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
    from app_apps.analysis.phase_control.envelope_handle import EnvelopeHandle


class PhaseControlService(SubprocessService):
    """
    Main-process service for the phase control subprocess.

    Owns the mode switch between phase-tracking and envelope control: only the
    active worker is registered as a buffer consumer, so paused workers never
    block slot release.
    """

    def __init__(
        self,
        bus: EventBus,
        spec: SpectrumMemorySpec,
        phase_tracking_handle: PhaseStabilizationHandle,
        envelope_handle: EnvelopeHandle,
        config: StabilizationConfig,
    ) -> None:
        super().__init__(bus)
        self.add_buffer(SpectrumBuffer, spec)
        self._phase_tracking_handle = phase_tracking_handle
        self._envelope_handle = envelope_handle
        self.add_handle(phase_tracking_handle)
        self.add_handle(envelope_handle)
        self._mode = ControlMode.PHASE_TRACKING
        self._config = config
        self._spectrum_unsub: Callable[[], None] | None = None

    @property
    def mode(self) -> ControlMode:
        return self._mode

    def set_config(self) -> None:
        self._phase_tracking_handle.set_config(self._config)

    def set_worker_paused(self, paused: bool) -> None:
        if self._mode == ControlMode.PHASE_TRACKING:
            self._phase_tracking_handle.set_paused(paused)
        else:
            self._envelope_handle.set_paused(paused)

    def reset_worker(self) -> None:
        if self._mode == ControlMode.PHASE_TRACKING:
            self._phase_tracking_handle.reset()
        else:
            self._envelope_handle.reset()

    def set_mode(self, mode: ControlMode) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        if mode == ControlMode.PHASE_TRACKING:
            self._envelope_handle.set_paused(True)
            self._phase_tracking_handle.set_paused(False)
        else:
            self._phase_tracking_handle.set_paused(True)
            self._envelope_handle.set_paused(False)

    @property
    def _entry_module(self) -> str:
        return "app_apps.analysis.phase_control.subprocess.phase_control_process"

    def start(self) -> None:
        super().start()
        self._spectrum_unsub = self._bus.subscribe(SpectrumAvailable, self._on_spectrum_available)

    def stop(self) -> None:
        if self._spectrum_unsub is not None:
            self._spectrum_unsub()
            self._spectrum_unsub = None
        super().stop()

    def _on_spectrum_available(self, event: SpectrumAvailable) -> None:
        if self._connector is not None:
            self._connector.send(ProcessSpectrum(
                slot=event.slot,
                item_id=event.item_id,
                timestamp_ns=event.timestamp_ns,
            ))
