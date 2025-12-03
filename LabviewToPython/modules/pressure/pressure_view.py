from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

class PressureRootView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Pressure module (placeholder)"))
        lay.addStretch(1)
