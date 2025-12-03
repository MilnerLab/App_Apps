# modules/menu_module/layout/layout_view.py
from __future__ import annotations
from PySide6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QLabel
from LabviewToPython.modules.menu.viewmodels.layout_vm import LayoutViewModel

class LayoutDialog(QDialog):
    def __init__(self, vm: LayoutViewModel, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Layout")
        self.setModal(False)
        self._vm = vm

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Layout actions"))
        btn_save  = QPushButton("Save layout")
        btn_reset = QPushButton("Reset layout")
        btn_show  = QPushButton("Show all docks")
        lay.addWidget(btn_save); lay.addWidget(btn_reset); lay.addWidget(btn_show)
        lay.addStretch(1)

        btn_save.clicked.connect(self._vm.save)
        btn_reset.clicked.connect(self._vm.reset)
        btn_show.clicked.connect(self._vm.show_all)
