from __future__ import annotations
from typing import List
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from LabviewToPython.core.events.eventbus import IEventBus
from LabviewToPython.core.interfaces.i_module import IModule
from LabviewToPython.core.types.host import DockSpec
from LabviewToPython.modules.common.bases import DockModuleBase
from LabviewToPython.modules.scan.scan_view import ScanRootView

class ScanModule(DockModuleBase):
    def __init__(self) -> None:
        super().__init__()

    @property
    def module_id(self) -> str: return "scan"
    @property
    def display_name(self) -> str: return "Scan"

    def create_docks(self) -> List[DockSpec]:
        return [DockSpec(
            object_name="dock_scan",
            title="Scan",
            factory=lambda p: ScanRootView(p),
            area=Qt.DockWidgetArea.LeftDockWidgetArea,
        )]
