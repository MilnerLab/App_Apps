from __future__ import annotations

from PySide6.QtCore import Signal

from base_core.framework.app.service_status import ServiceStatus
from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_vm import PanelVM, ui_thread

from app_apps.analysis.phase_control.domain.mode import ControlMode
from app_apps.analysis.phase_control.subprocess.messages import CorrectionAvailable
from app_apps.analysis.phase_control.service import PhaseControlService


class PhaseControlVM(PanelVM):
    correction_updated = Signal(float)  # correction angle in degrees
    running_changed    = Signal(bool)
    paused_changed     = Signal(bool)

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        svc: PhaseControlService,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._svc    = svc
        self._paused = False
        self._running = svc.is_running
        self._sub(CorrectionAvailable, self._on_correction)
        self._sub(ServiceStatus, self._on_service_status)

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @ui_thread
    def _on_correction(self, event: CorrectionAvailable) -> None:
        self.correction_updated.emit(event.correction.angle.Deg)

    @ui_thread
    def _on_service_status(self, event: ServiceStatus) -> None:
        if event.name != PhaseControlService.service_name:
            return
        self._running = event.running
        if not event.running:
            self._paused = False
            self.paused_changed.emit(False)
        self.running_changed.emit(event.running)

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._svc.start()

    def stop(self) -> None:
        self._svc.stop()

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self._svc.set_paused(self._paused)
        self.paused_changed.emit(self._paused)

    def reset(self) -> None:
        self._svc.reset()

    def set_mode(self, mode: ControlMode) -> None:
        self._svc.set_active(mode)
