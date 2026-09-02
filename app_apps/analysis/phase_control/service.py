from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.subprocess_service import SubprocessService
from base_core.ipc.worker_handle import WorkerStatus
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
        phase_stabilization_handle: PhaseStabilizationHandle,
        envelope_handle: EnvelopeHandle,
        config: StabilizationConfig,
    ) -> None:
        super().__init__(bus)
        self.add_buffer(SpectrumBuffer, spec)
        self._phase_stabilization_handle = phase_stabilization_handle
        self._envelope_handle = envelope_handle
        self.add_handle(phase_stabilization_handle)
        self.add_handle(envelope_handle)
        self._mode = ControlMode.PHASE_TRACKING
        self._config = config
        self._spectrum_unsub: Callable[[], None] | None = None

    @property
    def mode(self) -> ControlMode:
        return self._mode

    @property
    def active_state(self) -> WorkerStatus:
        """The status of whichever worker is currently driving the HWP.

        The device panel's RGV interlock reads this: the operator must not turn the plate by
        hand while EITHER worker is running, and which one that is depends on the mode.
        """
        return self._active_handle().state

    def set_config(self) -> None:
        self._phase_stabilization_handle.set_config(self._config)

    def _active_handle(self) -> PhaseStabilizationHandle | EnvelopeHandle:
        return self._phase_stabilization_handle if self._mode == ControlMode.PHASE_TRACKING else self._envelope_handle

    def set_worker_paused(self, paused: bool) -> None:
        handle = self._active_handle()
        handle.pause() if paused else handle.resume()

    def stop_worker(self) -> None:
        self._active_handle().stop()

    def set_mode(self, mode: ControlMode) -> None:
        if mode == self._mode:
            return
        old_active = self._active_handle()
        self._mode = mode
        new_active = self._active_handle()
        old_active.pause()
        if new_active.state == WorkerStatus.PAUSED:
            new_active.resume()

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
