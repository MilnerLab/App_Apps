# -------------------------------
# Length (intern immer in m)
# -------------------------------
from typing import Optional

from LabviewToPython.modules.menu.menu_services.units_service import UnitsService


@dataclass(frozen=True)
class Length:
    _m: float
    _provider: Optional[UnitsService] = None

    def __init__(self, value: float, system: LengthSystem, provider: Optional[UnitsProvider] = None) -> None:
        # interpret value gemäß übergebenem System (metric:=m, imperial:=inch)
        object.__setattr__(self, "_m", display_to_meters(value, system))
        object.__setattr__(self, "_provider", provider)

    @classmethod
    def from_meters(cls, meters: float, provider: Optional[UnitsProvider] = None) -> "Length":
        return cls.__new_with_state(meters, provider)

    # value in aktuell eingestelltem System (m oder in)
    @property
    def value(self) -> float:
        sys = self._display_length_system()
        v, _ = meters_to_display(self._m, sys)
        return v

    @property
    def unit(self) -> str:
        sys = self._display_length_system()
        return "m" if sys == LengthSystem.METRIC else "in"
 
    # explizit in Meter oder in gewünschtes System
    def meters(self) -> float:
        return self._m

    def as_system(self, system: LengthSystem) -> float:
        v, _ = meters_to_display(self._m, system)
        return v

    def __add__(self, other: "Length") -> "Length":
        return Length.from_meters(self._m + other._m, self._provider or other._provider)

    def __sub__(self, other: "Length") -> "Length":
        return Length.from_meters(self._m - other._m, self._provider or other._provider)

    @classmethod
    def __new_with_state(cls, meters: float, provider: Optional[UnitsProvider]) -> "Length":
        self = object.__new__(cls)
        object.__setattr__(self, "_m", meters)
        object.__setattr__(self, "_provider", provider)
        return self

    def _display_length_system(self) -> LengthSystem:
        if self._provider is not None:
            return self._provider.length_system()
        val = QSettings().value("units/length_system", LengthSystem.METRIC.value)
        try:
            return LengthSystem(str(val))
        except Exception:
            return LengthSystem.METRIC
