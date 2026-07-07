from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QWidget

from base_core.ipc.worker_handle import WorkerStatus
from base_qt.ui.worker_control_widget import WorkerControlWidget
from app_apps.analysis.phase_control.ui.envelope_control_view_model import EnvelopeControlViewModel


class EnvelopeControlView(QWidget):
    def __init__(self, vm: EnvelopeControlViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        row.addStretch()

        self._worker_ctrl = WorkerControlWidget(vm.start, vm.pause, vm.resume, vm.stop)
        self._worker_ctrl.set_status(vm.worker_state)
        row.addWidget(self._worker_ctrl)

        vm.worker_state_changed.connect(self._on_worker_state_changed)

    def _on_worker_state_changed(self, status: WorkerStatus) -> None:
        self._worker_ctrl.set_status(status)
