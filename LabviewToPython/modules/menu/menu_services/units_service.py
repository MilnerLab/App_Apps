from __future__ import annotations
from LabviewToPython.core.enums.length_system import LengthSystem
from LabviewToPython.core.enums.pressure_unit import PressureUnit

class UnitsService:

    def __init__(self) -> None:
        self._length = LengthSystem.METRIC
        self._pressure = PressureUnit.BAR

    @property
    def length_system(self) -> LengthSystem:
        return self._length
    @length_system.setter
    def length_system(self, system: LengthSystem) -> None:
        if system == self._length: return
        self._length = system

    @property
    def pressure_unit(self) -> PressureUnit:
        return self._pressure
    @pressure_unit.setter
    def pressure_unit(self, unit: PressureUnit) -> None:
        if unit == self._pressure: return
        self._pressure = unit
    

    
