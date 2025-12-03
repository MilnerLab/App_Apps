from __future__ import annotations
from typing import List
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from LabviewToPython.core.types.host import DockSpec
from LabviewToPython.modules.common.bases import DockModuleBase
from LabviewToPython.modules.pressure.pressure_view import PressureRootView

class PressureModule(DockModuleBase):
    def __init__(self) -> None:
        super().__init__()

    @property
    def module_id(self) -> str: return "pressure"
    @property
    def display_name(self) -> str: return "Pressure"

    def create_docks(self) -> List[DockSpec]:
        return [DockSpec(
            object_name="dock_pressure",
            title="Pressure",
            factory=lambda p: PressureRootView(p),
            area=Qt.DockWidgetArea.RightDockWidgetArea,
        )]
