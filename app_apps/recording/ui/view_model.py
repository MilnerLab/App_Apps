from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread

from app_apps.recording.events import SpectrumRecordingStateChanged
from app_apps.recording.spectrum_recording_service import SpectrumRecordingService


class SpectrumRecordingViewModel(PanelViewModel):
    """Start/stop a standalone spectrometer recording and read its counters."""

    state_changed = Signal()

    def __init__(self, bus: EventBus, dispatcher: QtDispatcher,
                 service: SpectrumRecordingService) -> None:
        super().__init__(bus, dispatcher)
        self._service = service
        self._sub(SpectrumRecordingStateChanged, self._on_state_changed)

    @property
    def is_recording(self) -> bool:
        return self._service.is_recording

    @property
    def path(self) -> Path | None:
        return self._service.path

    def counters(self) -> tuple[int, int, int]:
        """``(traces, metadata entries, dropped)`` of the active recording, zeros when idle."""
        rec = self._service.recorder
        if rec is None:
            return 0, 0, 0
        return rec.n_traces, rec.n_metadata, rec.n_dropped

    def start(self, out_dir: str, name: str) -> None:
        try:
            path = self._service.start(Path(out_dir or "."), name)
        except Exception as exc:
            self._msg(f"Spectrum recording failed to start: {exc}", MessageLevel.ERROR)
            return
        self._msg(f"Recording spectra to {path}")

    def stop(self) -> None:
        self._service.stop()

    @ui_thread
    def _on_state_changed(self, _: SpectrumRecordingStateChanged) -> None:
        self.state_changed.emit()
