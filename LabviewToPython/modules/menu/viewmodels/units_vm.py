from __future__ import annotations
from PySide6.QtCore import QObject, Signal, Slot
from LabviewToPython.core.enums.length_system import LengthSystem
from LabviewToPython.core.enums.pressure_unit import PressureUnit
from ..menu_services.units_service import UnitsService

class UnitsViewModel():

    def __init__(self, units: UnitsService) -> None:
        super().__init__()
        self._units = units

    # ---- length ----
    def length_system(self) -> LengthSystem:
        return self._units.length_system()

    def is_metric(self) -> bool:
        return self._units.length_system() == LengthSystem.METRIC

    @Slot()
    def setMetric(self) -> None:
        self._units.set_length_system(LengthSystem.METRIC)

    @Slot()
    def setImperial(self) -> None:
        self._units.set_length_system(LengthSystem.IMPERIAL)

    # ---- pressure ----
    def pressure_unit(self) -> PressureUnit:
        return self._units.pressure_unit()

    @Slot(object)
    def setPressure(self, unit: PressureUnit) -> None:
        self._units.set_pressure_unit(unit)
