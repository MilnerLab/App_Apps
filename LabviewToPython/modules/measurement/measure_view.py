from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from LabviewToPython.modules.measurement.measure_vm import MeasureViewModel

class MeasurementView(QWidget):
    """Top-level view for the Measurement module."""
    def __init__(self, vm: MeasureViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vm = vm

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Measurement module (placeholder)"))
        lay.addStretch(1)
