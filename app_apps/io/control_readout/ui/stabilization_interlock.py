from __future__ import annotations

from typing import Protocol

from PySide6.QtWidgets import QMessageBox, QWidget


class StabilizationInterlock(Protocol):
    """What a rotator view model exposes when the phase loop may be driving its plate."""

    @property
    def stabilization_running(self) -> bool: ...

    def stop_stabilization(self) -> None: ...


def confirm_stop_stabilization(parent: QWidget, vm: StabilizationInterlock,
                               description: str) -> bool:
    """The ``confirm_move`` hook for a plate the loop may own: ask, and on Yes stop the loop
    BEFORE the move goes out, so the operator and the loop never both command the plate."""
    if not vm.stabilization_running:
        return True
    answer = QMessageBox.question(
        parent,
        "Stabilization is running",
        f"The phase loop is driving this plate.\n\n"
        f"Stop stabilization and {description}?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    vm.stop_stabilization()
    return True
