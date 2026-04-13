from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from base_qt.views.bases.main_window_view_base import MainWindowViewBase
from base_qt.views.registry.interfaces import IViewRegistry

from .main_window_vm import MainWindowVM


class MainWindowView(MainWindowViewBase[MainWindowVM]):
    def __init__(
        self,
        vm: MainWindowVM,
        registry: IViewRegistry,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            vm=vm,
            registry=registry,
            parent=parent,
            title="App",
        )

    def build_ui(self) -> None:
        super().build_ui()

        self.resize(1200, 800)

        layout = QVBoxLayout(self.central)
        layout.setContentsMargins(8, 8, 8, 8)

        label = QLabel("App workspace")
        layout.addWidget(label)
        layout.addStretch(1)