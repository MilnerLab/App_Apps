"""Centrifuge rotational frequency from a fitted spectrum.

The SPM-002 records the centrifuge field through a polarizer. Mapping the spectrum's wavelength
axis to time (via the pulse chirp) turns the spectral fringes into the time-domain,
polarizer-modulated rotation signal. By Malus's law the transmitted intensity goes as
``cos^2(theta) = (1 + cos 2*theta)/2`` — it peaks every ``pi`` of rotation, so the observed
oscillation is at **twice** the rotation frequency. Hence:

    rotation = polarizer_factor (= 1/2) * observed_time_frequency

The observed time frequency at a given optical frequency ``nu`` is the *local* spectral fringe
rate (cycles per THz) times the chirp rate ``dnu/dt`` (THz per ps):

    local_fringe_rate(nu) = tau_ps + 2*g2*dnu + 3*g3*dnu**2      (dnu = nu - nu0)   [cycles/THz]
    f_time(nu)            = local_fringe_rate(nu) * chirp_rate    [cycles/ps == THz]
    rotation_ghz(nu)      = polarizer_factor * |f_time(nu)| * 1e3 [GHz]

Pure (numpy-free) and additive. The chirp rate and polarizer factor are pluggable via
``PulseChirp`` — **[PHYSICS-CONFIRM]**: this uses a leading-order linear chirp derived from the
pulse FWHMs; replace with a measured chirp calibration when available.
"""
from __future__ import annotations

from dataclasses import dataclass

from app_apps.analysis.spectrum_info.model import C_NM_THZ, SpectrumInfo


@dataclass(frozen=True)
class PulseChirp:
    """Pulse/chirp calibration for the spectrum->rotation conversion. [PHYSICS-CONFIRM]"""

    temporal_fwhm_ps: float = 300.0
    central_wavelength_nm: float = 802.0
    optical_fwhm_nm: float = 8.0
    polarizer_factor: float = 0.5  # rotation = factor * observed (Malus cos^2 -> /2)

    @property
    def optical_fwhm_thz(self) -> float:
        """Optical FWHM expressed in THz: |dnu| = c * dlambda / lambda^2."""
        return C_NM_THZ * self.optical_fwhm_nm / self.central_wavelength_nm**2

    @property
    def chirp_rate_thz_per_ps(self) -> float:
        """Leading-order linear chirp rate dnu/dt (THz/ps) from the pulse FWHMs (~0.0124 here)."""
        return self.optical_fwhm_thz / self.temporal_fwhm_ps


def local_fringe_rate_cycles_per_thz(info: SpectrumInfo, nu_thz_at: float) -> float:
    """Local spectral fringe rate d(phase)/d(nu)/(2*pi) at optical frequency ``nu_thz_at``."""
    dnu = nu_thz_at - info.nu0_thz
    return info.tau_ps + 2.0 * info.g2 * dnu + 3.0 * info.g3 * dnu**2


def rotational_frequency_ghz(
    info: SpectrumInfo, nu_thz_at: float, chirp: PulseChirp = PulseChirp()
) -> float:
    """Centrifuge rotational frequency (GHz) at optical frequency ``nu_thz_at``."""
    rate = local_fringe_rate_cycles_per_thz(info, nu_thz_at)  # cycles/THz
    f_time_thz = rate * chirp.chirp_rate_thz_per_ps           # cycles/ps == THz
    return chirp.polarizer_factor * abs(f_time_thz) * 1e3     # GHz


def terminal_rotational_frequency_ghz(
    info: SpectrumInfo, chirp: PulseChirp = PulseChirp()
) -> float:
    """Rotational frequency at the red (truncation) edge — the terminal centrifuge frequency."""
    return rotational_frequency_ghz(info, info.nu_end_thz, chirp)


def start_rotational_frequency_ghz(
    info: SpectrumInfo, chirp: PulseChirp = PulseChirp()
) -> float:
    """Rotational frequency at the blue (start) edge."""
    return rotational_frequency_ghz(info, info.nu_start_thz, chirp)
