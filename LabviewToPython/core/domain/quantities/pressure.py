from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, cast

from LabviewToPython.core.enums.pressure_unit import PressureUnit
from LabviewToPython.core.di.container import Container
from LabviewToPython.modules.menu.menu_services.units_service import UnitsService

# ---- pressure helpers ----
PA_PER_BAR = 1.0e5
PA_PER_TORR = 133.322368
PA_PER_PSI = 6894.757293
PA_PER_INHG = 3386.389


# -------------------------------
# Pressure (intern immer in Pa)
# -------------------------------
@dataclass(frozen=True)
class Pressure:
    _pa: float
    _provider: UnitsService

    def __init__(self, value: float, unit: PressureUnit, provider: UnitsService) -> None:
        object.__setattr__(self, "_pa", unit_to_pa(value, unit))
        provider = Container.try_get(UnitsService)
        object.__setattr__(self, "_provider", provider)

    # value in aktuell eingestellter Einheit
    @property
    def value(self) -> float:
        u = self._provider.pressure_unit()
        v, _ = pa_to_unit(self._pa, u)
        return v

    # Einheit als String (z. B. "bar")
    @property
    def unit(self) -> str:
        return self._provider.pressure_unit()


def pa_to_unit(value_pa: float, unit: PressureUnit) -> tuple[float, str]:
    if unit == PressureUnit.PA:   return value_pa, "Pa"
    if unit == PressureUnit.BAR:  return value_pa / PA_PER_BAR, "bar"
    if unit == PressureUnit.TORR: return value_pa / PA_PER_TORR, "Torr"
    if unit == PressureUnit.PSI:  return value_pa / PA_PER_PSI, "psi"
    if unit == PressureUnit.INHG: return value_pa / PA_PER_INHG, "inHg"
    return value_pa, "Pa"

def unit_to_pa(value: float, unit: PressureUnit) -> float:
    if unit == PressureUnit.PA:   return value
    if unit == PressureUnit.BAR:  return value * PA_PER_BAR
    if unit == PressureUnit.TORR: return value * PA_PER_TORR
    if unit == PressureUnit.PSI:  return value * PA_PER_PSI
    if unit == PressureUnit.INHG: return value * PA_PER_INHG
    return value
