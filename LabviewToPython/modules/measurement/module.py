# LabviewToPython/modules/measurement/module.py
from __future__ import annotations

from typing import List, Optional
from PySide6.QtCore import Qt

from LabviewToPython.core.types.host import DockSpec, AppContext
from LabviewToPython.modules.common.bases import DockModuleBase
from LabviewToPython.modules.measurement.measure_view import MeasurementView
from LabviewToPython.modules.measurement.measure_vm import MeasureViewModel


class MeasurementModule(DockModuleBase):
    """
    Provides the 'Measurement' dock (camera, cos^2(theta) pipeline, etc.).
    Implements only the dock provider interface; no menus here.
    """

    def __init__(self) -> None:
        super().__init__()
        self._measurement_vm: Optional[MeasureViewModel] = None

    @property
    def measurement_vm(self) -> MeasureViewModel:
        assert self._measurement_vm is not None, "MeasureViewModel not initialized."
        return self._measurement_vm
    
    # lifecycle: services / VM init
    def start(self, ctx: AppContext) -> None:
        super().start(ctx)
        # If your VM does not take bus/host, drop the args accordingly.
        self._measurement_vm = MeasureViewModel(bus=self.bus)

    # UI contribution: create docks for the host
    def create_docks(self) -> List[DockSpec]:
        return [
            DockSpec(
                object_name="dock_measurement",
                title="Measurement",
                factory=lambda parent: MeasurementView(self.measurement_vm),
                area=Qt.DockWidgetArea.LeftDockWidgetArea,
            )
        ]
