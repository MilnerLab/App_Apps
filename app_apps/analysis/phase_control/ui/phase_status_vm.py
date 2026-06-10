from __future__ import annotations

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_vm import PanelVM, ui_thread
from base_qt.ui.app_message import MessageLevel

from app_apps.analysis.phase_control.domain.mode import ControlMode
from app_apps.analysis.phase_control.subprocess.messages import CorrectionAvailable
from app_apps.analysis.phase_control.service import PhaseControlService


class PhaseStatusVM(PanelVM):
    phase_updated = Signal(float, float)  # (phase_deg, correction_deg)
    paused_changed = Signal(bool)

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        svc: PhaseControlService,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._svc    = svc
        self._paused = False
        self._sub(CorrectionAvailable, self._on_correction)

    @ui_thread
    def _on_correction(self, event: CorrectionAvailable) -> None:
        self.phase_updated.emit(event.phase_deg, event.correction_deg)

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def set_mode(self, mode: ControlMode) -> None:
        self._svc.set_active(mode)

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self._svc.set_paused(self._paused)
        self.paused_changed.emit(self._paused)

    def reset(self) -> None:
        self._svc.reset()
        self._msg("Phase tracking reset", MessageLevel.INFO)
