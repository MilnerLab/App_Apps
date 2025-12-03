from typing import List, Type
from LabviewToPython.core.interfaces.i_module import IModule
from LabviewToPython.modules.measurement.module import MeasurementModule
from LabviewToPython.modules.scan.module import ScanModule
from LabviewToPython.modules.pressure.module import PressureModule
from LabviewToPython.modules.control.module import ControlModule
from LabviewToPython.modules.menu.module import MenuModule

MODULES: List[Type[IModule]] = [
    MeasurementModule,
    ScanModule,
    PressureModule,
    ControlModule,
    MenuModule,   # Menüs nach den Docks
]
