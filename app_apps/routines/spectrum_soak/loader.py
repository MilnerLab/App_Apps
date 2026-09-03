"""Read a finished soak back off disk and into the panel's heatmap.

The other door into :class:`SpectrumSoakView`: live recording draws itself as it goes,
this turns a ``SOAK_*.h5`` back into the same picture so an old run can be looked at
next to a new one. That comparison -- loop off yesterday against loop on today -- is the
whole reason the files exist, and it is not much use if the only way to see one is to
have been watching while it was written.

Pure numpy/h5py, no Qt. Every entry point either returns a :class:`LoadedSoak` or raises
:class:`SoakLoadError` with a message fit to put in front of an operator.

**A recording in progress holds its file open.** Windows denies the shared read, and
HDF5 reports it as a lock failure, so that case is named rather than surfacing as a raw
``OSError``. Snapshotting a locked file is deliberately not attempted: a copy taken
mid-flush is not a valid HDF5 file.

**Long soaks are decimated on the way in, not after.** A file recorded at period 0 can
hold tens of thousands of rows; the panel draws at most a few thousand. Striding at read
time means the whole array is never materialised, and the stride is reported so the
picture can say what it is showing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import h5py
import numpy as np

log = logging.getLogger(__name__)

#: Format written by ``recorder.SoakH5Writer``. Anything else is refused up front -- a
#: wrong-schema file would otherwise fail deep inside with a KeyError.
FORMAT_NAME = "milnerlab-spectrum-soak"


class SoakLoadError(RuntimeError):
    """The file is missing, locked, not a soak, or holds no spectra."""


@dataclass
class LoadedSoak:
    """One recording, as far as the panel is concerned."""

    path: Path
    wavelength_nm: np.ndarray            # [px]
    counts: np.ndarray                   # [rows, px], float32, possibly decimated
    timestamp_ns: np.ndarray             # [rows], int64, matching counts row for row
    n_rows_total: int                    # rows in the FILE, before any decimation
    stride: int                          # 1 when nothing was skipped
    #: One row per correction the phase loop commanded during the run, in order:
    #: (timestamp_ns, angle_deg, after_row). ``after_row`` counts rows in the FILE, so it
    #: must be divided by ``stride`` before it indexes ``counts``. Empty for a file written
    #: before the correction log existed, which is indistinguishable from a run where the
    #: loop was off -- ``stabilizing`` is what tells those apart.
    corrections: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    attrs: dict = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        """Wall-clock span of the recording. 0 for a single row."""
        if self.timestamp_ns.size < 2:
            return 0.0
        return float(self.timestamp_ns[-1] - self.timestamp_ns[0]) / 1e9

    @property
    def recorded_roi_nm(self) -> str:
        """The band recorded, as stamped at the time. Empty when the whole detector was."""
        return str(self.attrs.get("recorded_roi_nm", "") or "")

    def summary(self) -> str:
        """One line for the status bar."""
        span = f"{self.wavelength_nm[0]:.2f}-{self.wavelength_nm[-1]:.2f} nm"
        roi = self.recorded_roi_nm
        bits = [f"{self.n_rows_total} spectra", f"{self.duration_s:.0f} s", span]
        if roi:
            bits.append(f"ROI {roi}")
        if self.stride > 1:
            bits.append(f"showing every {self.stride}th")
        if self.corrections.size:
            bits.append(f"{self.corrections.shape[0]} corrections")
        if self.attrs.get("n_dropped"):
            bits.append(f"{int(self.attrs['n_dropped'])} dropped")
        return f"{self.path.name}: " + " · ".join(bits)


def _open(path: Path) -> h5py.File:
    """Open read-only, translating the two failure modes an operator will actually hit."""
    if not path.exists():
        raise SoakLoadError(f"No such file: {path}")
    try:
        return h5py.File(path, "r")
    except OSError as exc:
        if "unable to lock file" in str(exc) or "errno = 33" in str(exc):
            raise SoakLoadError(
                f"{path.name} is locked — the soak writing it is still recording. "
                f"Load it once the run finishes."
            ) from exc
        raise SoakLoadError(f"Could not open {path.name}: {exc}") from exc


def _scalar(v):
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def load_soak(path: str | Path, *, max_rows: int = 4000) -> LoadedSoak:
    """Read one ``SOAK_*.h5``. ``max_rows`` bounds what is returned, by striding.

    A soak that was stopped before its first spectrum has no datasets at all -- the
    writer creates them from the first frame's pixel count -- so that is reported as
    "no spectra" rather than as a missing key.
    """
    p = Path(path)
    with _open(p) as f:
        name = _scalar(f.attrs.get("format_name", ""))
        if name != FORMAT_NAME:
            raise SoakLoadError(
                f"{p.name} is not a spectrum soak (format_name={name!r}). "
                f"Soak files are written by the Spectrum Soak panel."
            )
        if "counts" not in f or "wavelength_nm" not in f:
            raise SoakLoadError(
                f"{p.name} holds no spectra — the recording ended before the first "
                f"frame arrived."
            )
        counts_ds = f["counts"]
        n_total = int(counts_ds.shape[0])
        if n_total == 0:
            raise SoakLoadError(f"{p.name} holds no spectra.")

        stride = max(1, -(-n_total // max(1, int(max_rows))))   # ceil
        wl = np.asarray(f["wavelength_nm"][:], dtype=np.float64)
        counts = np.asarray(counts_ds[::stride], dtype=np.float32)
        ts = (np.asarray(f["timestamp_ns"][::stride], dtype=np.int64)
              if "timestamp_ns" in f else np.zeros(counts.shape[0], dtype=np.int64))
        attrs = {k: _scalar(v) for k, v in f.attrs.items()}
        g = f.get("corrections")
        corr = (np.column_stack([np.asarray(g["timestamp_ns"][:], dtype=np.float64),
                                 np.asarray(g["angle_deg"][:], dtype=np.float64),
                                 np.asarray(g["after_row"][:], dtype=np.float64)])
                if g is not None and g["timestamp_ns"].shape[0] else np.zeros((0, 3)))

    log.info("loaded soak %s: %d of %d rows, %d px", p.name, counts.shape[0], n_total,
             wl.size)
    return LoadedSoak(path=p, wavelength_nm=wl, counts=counts, timestamp_ns=ts,
                      n_rows_total=n_total, stride=stride, attrs=attrs,
                      corrections=corr)
