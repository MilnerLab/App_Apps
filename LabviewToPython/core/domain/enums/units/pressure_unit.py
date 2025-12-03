from enum import Enum

class PressureUnit(str, Enum):
    PA   = "Pa"
    BAR  = "bar"
    TORR = "Torr"
    PSI  = "psi"
    INHG = "inHg"