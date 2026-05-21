from __future__ import annotations

import numpy as np

from base_core.framework.subprocess.shared_memory.models import SharedRingBufferSpec, SlotHeader
from base_core.framework.subprocess.shared_memory.shared_ring_buffer import SharedRingBuffer


class SharedSpectrumBuffer(SharedRingBuffer):
    """
    Payload layout per slot:
        frame[0, :] -> wavelengths
        frame[1, :] -> intensities

    The whole payload uses one dtype, exactly like SharedRingBufferSpec.
    So wavelengths and intensities must share the same dtype.
    """

    def __init__(
        self,
        *,
        spec: SharedRingBufferSpec,
        shm,
        owner: bool,
    ) -> None:
        super().__init__(spec=spec, shm=shm, owner=owner)

        if len(self.spec.shape) != 2 or self.spec.shape[0] != 2:
            raise ValueError(
                f"SharedSpectrumBuffer expects shape (2, N), got {self.spec.shape}."
            )

    @classmethod
    def create(
        cls,
        *,
        name: str,
        slot_count: int,
        pixel_count: int = 3648,
        dtype: str | np.dtype = np.float64,
    ) -> "SharedSpectrumBuffer":
        return super().create(
            name=name,
            slot_count=slot_count,
            shape=(2, pixel_count),
            dtype=dtype,
        )

    @property
    def pixel_count(self) -> int:
        return self.spec.shape[1]

    def wavelengths_view(self, slot: int) -> np.ndarray:
        return self.payload_view(slot)[0, :]

    def intensities_view(self, slot: int) -> np.ndarray:
        return self.payload_view(slot)[1, :]

    def initialize_wavelengths(self, wavelengths: np.ndarray) -> None:
        """
        Copy the wavelength axis into row 0 of all slots once.
        Call this explicitly on first setup from the writer process.
        """
        wavelengths = np.asarray(wavelengths, dtype=self.spec.np_dtype)

        if wavelengths.shape != (self.pixel_count,):
            raise ValueError(
                f"Wavelength shape mismatch: got {wavelengths.shape}, "
                f"expected {(self.pixel_count,)}."
            )

        if not wavelengths.flags["C_CONTIGUOUS"]:
            wavelengths = np.ascontiguousarray(wavelengths)

        for slot in range(self.spec.slot_count):
            self.wavelengths_view(slot)[:] = wavelengths

    def write_spectrum(
        self,
        *,
        slot: int,
        intensities: np.ndarray,
        frame_id: int,
        timestamp_ns: int,
        wavelengths: np.ndarray | None = None,
    ) -> None:
        """
        If wavelengths are provided, row 0 is refreshed in all slots first.
        Afterwards only row 1 of the selected slot is updated.

        Typical usage:
            first call  -> pass wavelengths=...
            later calls -> omit wavelengths
        """
        if wavelengths is not None:
            self.initialize_wavelengths(wavelengths)

        intensities = np.asarray(intensities, dtype=self.spec.np_dtype)

        if intensities.shape != (self.pixel_count,):
            raise ValueError(
                f"Intensity shape mismatch: got {intensities.shape}, "
                f"expected {(self.pixel_count,)}."
            )

        if not intensities.flags["C_CONTIGUOUS"]:
            intensities = np.ascontiguousarray(intensities)

        self.intensities_view(slot)[:] = intensities

        self.write_header(
            slot,
            SlotHeader(
                frame_id=frame_id,
                timestamp_ns=timestamp_ns,
                payload_nbytes=self.spec.slot_payload_size,
            ),
        )

    def read_spectrum_view(self, slot: int) -> tuple[SlotHeader, np.ndarray, np.ndarray]:
        header, frame = self.read_frame_view(slot)
        return header, frame[0, :], frame[1, :]

    def read_spectrum_copy(self, slot: int) -> tuple[SlotHeader, np.ndarray, np.ndarray]:
        header, frame = self.read_frame_copy(slot)
        return header, frame[0, :].copy(), frame[1, :].copy()