from enum import Enum

class LengthSystem(str, Enum):
    METRIC = "metric"     # meters
    IMPERIAL = "imperial" # inches/feet display (we'll return inches by default)
