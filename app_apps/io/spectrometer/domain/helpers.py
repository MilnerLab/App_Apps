import numpy as np


def normalize_spectrum(
    wavelengths_nm: np.ndarray, intensities: np.ndarray, wl_min: float, wl_max: float
) -> tuple[np.ndarray, np.ndarray]:
    mask = (wavelengths_nm >= wl_min) & (wavelengths_nm <= wl_max)
    wl = wavelengths_nm[mask]
    inten = intensities[mask].astype(float)
    inten -= inten.min()
    max_val = inten.max()
    if max_val > 0:
        inten /= max_val
    return wl, inten
