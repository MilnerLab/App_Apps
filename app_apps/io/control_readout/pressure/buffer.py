from __future__ import annotations

import numpy as np

from base_core.framework.shm.buffer import SharedMemoryBuffer
from base_core.framework.shm.spec import MemorySpec


class PressureMemorySpec(MemorySpec):
    def __init__(self, name: str, slot_count: int = 2, sensor_count: int = 4) -> None:
        super().__init__(name=name, slot_count=slot_count, shape=(sensor_count,), dtype="float64")

    @property
    def sensor_count(self) -> int:
        return self.shape[0]


class PressureBuffer(SharedMemoryBuffer):
    def write_pressures(self, slot: int, values: np.ndarray) -> None:
        self.write_slot(slot, np.asarray(values, dtype=np.float64))

    def read_pressures(self, slot: int) -> np.ndarray:
        return self.read_slot_copy(slot)
