"""
Append-only HDF5 store for wavelength↔probe-delay calibrations (M3.2 / D10).

SDS requirement: **never overwrite — add a new entry each time**. Each calibration is
written to a NEW group keyed by UTC timestamp + grating/delay-stage combination; an
index is reconstructed from the groups. Self-contained (h5py); can later adopt the
Base_Core ``h5_utils`` conventions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import h5py
import numpy as np

from app_apps.analysis.xcorr.calibration import WavelengthDelayCalibration

_ROOT = "calibrations"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize(text: str) -> str:
    # HDF5 link names may not contain '/'; keep names filesystem/HDF-safe.
    for bad in "/:\\ ":
        text = text.replace(bad, "-")
    return text


@dataclass(frozen=True)
class CalibrationEntry:
    key: str
    created_utc: str
    combination: str


class CalibrationStore:
    """Append-only calibration store backed by a single HDF5 file."""

    def __init__(self, path: str) -> None:
        self._path = str(path)

    # ------------------------------------------------------------------

    def append(self, cal: WavelengthDelayCalibration) -> str:
        """Write a NEW calibration entry; never overwrites. Returns its key."""
        base = f"{_sanitize(cal.created_utc)}__{_sanitize(cal.combination)}"
        with h5py.File(self._path, "a") as f:
            root = f.require_group(_ROOT)
            key = base
            n = 1
            while key in root:  # guarantee uniqueness -> never overwrite
                key = f"{base}__{n}"
                n += 1
            g = root.create_group(key)
            g.attrs["created_utc"] = cal.created_utc
            g.attrs["combination"] = cal.combination
            g.attrs["grating_stage"] = cal.grating_stage
            g.attrs["grating_position"] = float(cal.grating_position)
            g.attrs["delay_stage"] = cal.delay_stage
            g.attrs["delay_position"] = float(cal.delay_position)
            g.create_dataset("wavelengths_nm", data=np.asarray(cal.wavelengths_nm), compression="gzip")
            g.create_dataset("delays_ps", data=np.asarray(cal.delays_ps), compression="gzip")
        return key

    def entries(self) -> list[CalibrationEntry]:
        """All entries, newest first (by ``created_utc``)."""
        out: list[CalibrationEntry] = []
        with h5py.File(self._path, "a") as f:
            root = f.require_group(_ROOT)
            for key, g in root.items():
                out.append(CalibrationEntry(
                    key=key,
                    created_utc=str(g.attrs.get("created_utc", "")),
                    combination=str(g.attrs.get("combination", "")),
                ))
        out.sort(key=lambda e: e.created_utc, reverse=True)
        return out

    def count(self) -> int:
        return len(self.entries())

    def load(self, key: str) -> WavelengthDelayCalibration:
        with h5py.File(self._path, "r") as f:
            g = f[f"{_ROOT}/{key}"]
            return WavelengthDelayCalibration(
                created_utc=str(g.attrs["created_utc"]),
                grating_stage=str(g.attrs["grating_stage"]),
                grating_position=float(g.attrs["grating_position"]),
                delay_stage=str(g.attrs["delay_stage"]),
                delay_position=float(g.attrs["delay_position"]),
                wavelengths_nm=g["wavelengths_nm"][...],
                delays_ps=g["delays_ps"][...],
            )

    def latest(
        self,
        *,
        grating_stage: str | None = None,
        delay_stage: str | None = None,
    ) -> WavelengthDelayCalibration | None:
        """Most recent calibration, optionally filtered by stage(s)."""
        for entry in self.entries():
            cal = self.load(entry.key)
            if grating_stage is not None and cal.grating_stage != grating_stage:
                continue
            if delay_stage is not None and cal.delay_stage != delay_stage:
                continue
            return cal
        return None


def new_calibration(
    *,
    grating_stage: str,
    grating_position: float,
    delay_stage: str,
    delay_position: float,
    wavelengths_nm: np.ndarray,
    delays_ps: np.ndarray,
    created_utc: str | None = None,
) -> WavelengthDelayCalibration:
    """Convenience constructor that stamps ``created_utc`` if not provided."""
    return WavelengthDelayCalibration(
        created_utc=created_utc or _utc_now_iso(),
        grating_stage=grating_stage,
        grating_position=grating_position,
        delay_stage=delay_stage,
        delay_position=delay_position,
        wavelengths_nm=np.asarray(wavelengths_nm, dtype=float),
        delays_ps=np.asarray(delays_ps, dtype=float),
    )
