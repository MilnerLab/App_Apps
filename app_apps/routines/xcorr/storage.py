"""HDF5 run store for XCORR. Schema lives here; there is no separate schema module.

XCORR gets its own store rather than extending ``RunH5Store``/``schema.py``, which
model ion data and C2T keyed on ``run_id`` — a different domain. The *helpers* in
``h5_utils`` are shared; the schema is not (XCORR_SPEC.md §6.2).

Layout (§6.1)::

    XCORR_20260720_143022.h5
    ├─ (root attrs)  format_name, format_version, created_utc, run_id,
    │                aborted, completed_utc
    ├─ /config              one attr per XcorrConfig field
    ├─ /provenance/{esp301,scope}
    ├─ /scans/g####_d####/  one group per (grating, delay) combination
    │   ├─ (attrs) grating_mm, delay_mm, delay_base_mm, delay_correction_mm,
    │   │          probe_offset_mm, probe_step_mm, max_freq_ghz, grating_index,
    │   │          delay_index, n_traces_per_point, utc_start, utc_end, status
    │   ├─ probe_mm      float64[n]   (commanded; base = probe_mm - probe_offset_mm)
    │   ├─ v_mean_pos    float64[n]
    │   ├─ v_std         float64[n]
    │   └─ n_traces      int32[n]
    └─ /spectra             free-running spectrometer stream (format_version >= 2;
        │                   absent when no spectrometer was recording)
        ├─ (attrs) n_pixels, n_dropped, integration_span_ns, gate_rule, and the
        │          spectrometer settings in force
        ├─ wavelength_nm   float64[n_pixels]  written once
        ├─ counts          float32[N, n_pixels]
        ├─ timestamp_ns    int64[N]     time.time_ns() at end of integration
        ├─ setpoint_index  int32[N]
        ├─ probe_index     int32[N]
        ├─ grating_mm      float64[N]
        ├─ delay_mm        float64[N]
        └─ probe_mm        float64[N]   commanded

``/spectra`` rows are *not* aligned with the ``/scans`` rows: the spectrometer free-runs
at its own rate, so a probe point may carry zero, one or several spectra. Join on the
recorded stage positions (or on ``setpoint_index``/``probe_index``), never on row index.

There is deliberately no ``/plan`` table. It was a denormalised copy of what the scan
groups already carry, and it needed two workarounds — ``append_row`` cannot mutate a
row in place, and ``write_array`` throws on a zero-length array. "Which combinations
ran" is ``sorted(f["/scans"])``; "which didn't" is the planner re-run over ``/config``,
which is deterministic.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, fields as dataclass_fields
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Sequence

import h5py
import numpy as np

from base_core.framework.serialization.h5_utils import ensure_group, now_utc_iso

from app_apps.routines.xcorr.config import XcorrConfig
from app_apps.routines.xcorr.planner import ScanPlan, Setpoint

log = logging.getLogger(__name__)

FORMAT_NAME = "milnerlab-xcorr"
#: 2 added the optional ``/spectra`` group. Nothing in v1 changed, so a v2 file
#: without ``/spectra`` is byte-identical in structure to a v1 file and every v1
#: reader still works.
FORMAT_VERSION = 2

#: Rows appended to ``/spectra`` per resize. Resizing an HDF5 dataset is cheap but
#: not free, and the spectrometer delivers only a few per second.
_SPECTRA_CHUNK_ROWS = 32


def default_run_path(out_dir: Path, when: datetime | None = None) -> Path:
    """``<out_dir>/XCORR_YYYYmmdd_HHMMSS.h5`` — one file per run."""
    when = when or datetime.now()
    return Path(out_dir) / f"XCORR_{when:%Y%m%d_%H%M%S}.h5"


def _write_array(g: h5py.Group, name: str, arr: Sequence[Any], dtype: Any) -> None:
    """``h5_utils.write_array`` with the zero-length case handled (defect G17).

    ``write_array`` passes ``chunks=True, compression="lzf"`` unconditionally, and
    HDF5 rejects both on a zero-length dataset. A combination that was skipped or
    aborted before its first probe point produces exactly that, so an empty group
    would raise *while trying to record the failure*. Empty datasets are written
    plain and uncompressed instead; compression of nothing buys nothing anyway.
    """
    a = np.asarray(arr, dtype=dtype)
    if name in g:
        del g[name]
    if a.size == 0:
        g.create_dataset(name, data=a)
        return
    g.create_dataset(name, data=a, chunks=True, compression="lzf", shuffle=True)


@dataclass(frozen=True)
class SpectrumRecord:
    """One spectrum plus the stage positions in force when it was integrated.

    ``counts`` is whatever the spectrometer returned (float64 out of shared memory);
    it is narrowed to float32 on the way to disk. The device returns ``c_ushort``, so
    that is lossless here and halves the file.
    """

    counts: np.ndarray
    timestamp_ns: int
    setpoint_index: int
    probe_index: int
    grating_mm: float
    delay_mm: float
    probe_mm: float


class XcorrH5Writer:
    """Context manager owning one run file. Opened once, flushed per combination.

    The file is flushed after every ``(grating, delay)`` group and before the next
    combination is commanded (R4/§6.3), so a crash or an abort leaves a valid HDF5
    file containing every completed combination. Nothing is buffered to the end.

    ``__exit__`` always stamps ``completed_utc`` and closes, including on an
    exception — that, plus the routine's ``try/finally``, is what delivers R3.

    **Thread safety.** ``/scans`` is written from the routine thread and ``/spectra``
    from the spectrum recorder's writer thread. ``h5py`` is not thread-safe, so every
    method that touches the file takes :attr:`_lock`. The lock is held only for the
    duration of a write — a spectrum append is a few hundred microseconds, so it never
    meaningfully delays the per-setpoint flush that delivers the crash-safety
    guarantee (R4/§6.3).
    """

    def __init__(self, path: Path, run_id: str | None = None) -> None:
        self.path = Path(path)
        self.run_id = run_id or self.path.stem
        self._f: h5py.File | None = None
        self.n_groups_written = 0
        self.n_spectra_written = 0
        self._lock = threading.RLock()
        self._spectra: h5py.Group | None = None
        self._n_pixels = 0
        self._appends_since_flush = 0

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> "XcorrH5Writer":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = h5py.File(self.path, "a")
        self._f.attrs["format_name"] = FORMAT_NAME
        self._f.attrs["format_version"] = FORMAT_VERSION
        self._f.attrs["created_utc"] = now_utc_iso()
        self._f.attrs["run_id"] = self.run_id
        # Set up-front, so a file from a killed process is distinguishable from a
        # clean finish: aborted stays True and completed_utc stays empty.
        self._f.attrs["aborted"] = True
        self._f.attrs["completed_utc"] = ""
        ensure_group(self._f, "/scans")
        self._f.flush()
        log.info("XCORR run file opened: %s", self.path)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        with self._lock:
            if self._f is None:
                return
            try:
                self._f.attrs["completed_utc"] = now_utc_iso()
                self._f.flush()
            finally:
                self._spectra = None
                self._f.close()
                self._f = None
        log.info(
            "XCORR run file closed: %s (%d group(s), %d spectra)",
            self.path, self.n_groups_written, self.n_spectra_written,
        )

    @property
    def file(self) -> h5py.File:
        if self._f is None:
            raise RuntimeError("XcorrH5Writer is not open — use it as a context manager")
        return self._f

    # -- header -----------------------------------------------------------

    def write_config(self, cfg: XcorrConfig, plan: ScanPlan) -> None:
        """One attribute per config field, plus the plan-time decisions.

        Storing ``outer_axis`` matters: group *names* sort in grid order, which is
        only execution order when the grating took the outer loop, so this is the
        record of what actually ran in what sequence.
        """
        with self._lock:
            g = ensure_group(self.file, "/config")
            for f in dataclass_fields(cfg):
                value = getattr(cfg, f.name)
                g.attrs[f.name] = str(value) if isinstance(value, Path) else value

            g.attrs["outer_axis"] = plan.outer_axis
            g.attrs["outer_reason"] = plan.outer_reason
            g.attrs["n_setpoints"] = len(plan.setpoints)
            g.attrs["n_points_total"] = plan.n_points
            # Probe point count is per-setpoint under adaptive stepping, so record the
            # step range rather than a single count (which no longer exists run-wide).
            fine, coarse = plan.probe_step_range_mm
            g.attrs["probe_step_min_mm"] = fine
            g.attrs["probe_step_max_mm"] = coarse
            g.attrs["plan_warnings"] = list(plan.warnings)
            self.file.flush()

    def write_provenance(self, section: str, values: dict[str, Any]) -> None:
        """Record ``/provenance/<section>`` — instrument state as found (R5).

        Provenance is read, never written: the scope's front-panel state is
        operator-owned and correct as left (§3.3.1).
        """
        with self._lock:
            g = ensure_group(self.file, f"/provenance/{section}")
            for k, v in values.items():
                g.attrs[k] = str(v) if isinstance(v, Path) else v
            self.file.flush()

    # -- per-combination scan groups (R4) ---------------------------------

    def write_group(
        self,
        setpoint: Setpoint,
        rows: Sequence[tuple[float, float, float, int]],
        *,
        n_traces_per_point: int,
        utc_start: str,
        status: str = "ok",
    ) -> str:
        """Write and flush one ``(grating, delay)`` combination.

        ``rows`` is ``(probe_mm, v_mean_pos, v_std, n_traces)`` per probe point. An
        empty ``rows`` is legal and produces an empty, self-describing group — that
        is how a skipped or aborted-early combination records itself (N3).

        Returns the group name. Flushes before returning, so the caller may command
        the next combination immediately.
        """
        with self._lock:
            return self._write_group_locked(
                setpoint, rows,
                n_traces_per_point=n_traces_per_point,
                utc_start=utc_start,
                status=status,
            )

    def _write_group_locked(
        self,
        setpoint: Setpoint,
        rows: Sequence[tuple[float, float, float, int]],
        *,
        n_traces_per_point: int,
        utc_start: str,
        status: str,
    ) -> str:
        scans = ensure_group(self.file, "/scans")
        name = setpoint.group_name
        if name in scans:
            del scans[name]
        g = scans.create_group(name)

        g.attrs["grating_mm"] = setpoint.grating_mm
        g.attrs["delay_mm"] = setpoint.delay_mm
        g.attrs["delay_base_mm"] = setpoint.delay_base_mm
        g.attrs["delay_correction_mm"] = setpoint.delay_correction_mm
        # probe_mm holds the *commanded* positions; subtract this to recover the
        # grating-independent base delay axis (probe_offset = grating + intercept).
        g.attrs["probe_offset_mm"] = setpoint.probe_offset_mm
        # This setpoint's Nyquist-matched sampling and the frequency it was matched to.
        g.attrs["probe_step_mm"] = setpoint.probe_step_mm
        g.attrs["max_freq_ghz"] = setpoint.max_freq_ghz
        g.attrs["grating_index"] = setpoint.grating_index
        g.attrs["delay_index"] = setpoint.delay_index
        g.attrs["n_traces_per_point"] = n_traces_per_point
        g.attrs["utc_start"] = utc_start
        g.attrs["utc_end"] = now_utc_iso()
        g.attrs["status"] = status

        _write_array(g, "probe_mm", [r[0] for r in rows], np.float64)
        _write_array(g, "v_mean_pos", [r[1] for r in rows], np.float64)
        _write_array(g, "v_std", [r[2] for r in rows], np.float64)
        _write_array(g, "n_traces", [r[3] for r in rows], np.int32)

        self.file.flush()
        self.n_groups_written += 1
        log.info("XCORR wrote /scans/%s (%d point(s), status=%s)", name, len(rows), status)
        return name

    # -- the free-running spectrometer stream -----------------------------

    def open_spectra(self, wavelengths: Sequence[float], attrs: dict[str, Any]) -> None:
        """Create ``/spectra`` and pin the wavelength axis. Idempotent per run.

        Deferred until the first spectrum arrives because ``n_pixels`` comes from the
        device, not from configuration — and because a run where the spectrometer never
        delivered anything should have no ``/spectra`` group at all rather than an empty
        one claiming a pixel count nobody measured.
        """
        with self._lock:
            if self._spectra is not None:
                return
            wl = np.asarray(wavelengths, dtype=np.float64)
            n_pixels = int(wl.size)
            if n_pixels == 0:
                raise ValueError("open_spectra: empty wavelength axis")

            g = ensure_group(self.file, "/spectra")
            for k, v in attrs.items():
                g.attrs[k] = str(v) if isinstance(v, Path) else v
            g.attrs["n_pixels"] = n_pixels
            g.attrs["n_dropped"] = 0

            _write_array(g, "wavelength_nm", wl, np.float64)
            # Row-chunked: readers pull individual spectra, and a chunk that spans rows
            # would make a single-spectrum read decompress its neighbours too.
            g.create_dataset(
                "counts",
                shape=(0, n_pixels), maxshape=(None, n_pixels), dtype=np.float32,
                chunks=(1, n_pixels), compression="lzf", shuffle=True,
            )
            for name, dtype in (
                ("timestamp_ns", np.int64),
                ("setpoint_index", np.int32),
                ("probe_index", np.int32),
                ("grating_mm", np.float64),
                ("delay_mm", np.float64),
                ("probe_mm", np.float64),
            ):
                g.create_dataset(
                    name,
                    shape=(0,), maxshape=(None,), dtype=dtype,
                    chunks=(_SPECTRA_CHUNK_ROWS,), compression="lzf", shuffle=True,
                )

            self._spectra = g
            self._n_pixels = n_pixels
            self.file.flush()
            log.info("XCORR /spectra opened (%d pixels)", n_pixels)

    def append_spectra(self, records: Sequence[SpectrumRecord]) -> int:
        """Append a batch of spectra. Returns the number actually written.

        A record whose pixel count disagrees with the axis pinned by
        :meth:`open_spectra` is skipped with a warning rather than aborting the run —
        a mid-run spectrometer reconfiguration must not cost the XCORR data.

        Flushed every ``_SPECTRA_CHUNK_ROWS`` appends rather than per batch: the
        per-setpoint ``write_group`` flush is what carries the crash-safety guarantee,
        and flushing the whole file a few times a second would fight it.
        """
        with self._lock:
            g = self._spectra
            if g is None or self._f is None or not records:
                return 0

            good = [r for r in records if np.asarray(r.counts).size == self._n_pixels]
            if len(good) != len(records):
                log.warning(
                    "XCORR /spectra: skipped %d spectrum/spectra with unexpected pixel count "
                    "(axis is %d wide)",
                    len(records) - len(good), self._n_pixels,
                )
            if not good:
                return 0

            start = g["counts"].shape[0]
            end = start + len(good)

            g["counts"].resize(end, axis=0)
            g["counts"][start:end, :] = np.asarray(
                [np.asarray(r.counts, dtype=np.float32) for r in good], dtype=np.float32
            )
            for name, dtype, get in (
                ("timestamp_ns", np.int64, lambda r: r.timestamp_ns),
                ("setpoint_index", np.int32, lambda r: r.setpoint_index),
                ("probe_index", np.int32, lambda r: r.probe_index),
                ("grating_mm", np.float64, lambda r: r.grating_mm),
                ("delay_mm", np.float64, lambda r: r.delay_mm),
                ("probe_mm", np.float64, lambda r: r.probe_mm),
            ):
                g[name].resize(end, axis=0)
                g[name][start:end] = np.asarray([get(r) for r in good], dtype=dtype)

            self.n_spectra_written = end
            self._appends_since_flush += len(good)
            if self._appends_since_flush >= _SPECTRA_CHUNK_ROWS:
                self._appends_since_flush = 0
                self.file.flush()
            return len(good)

    def close_spectra(self, *, n_dropped: int) -> None:
        """Stamp the drop count and flush. Safe to call when ``/spectra`` was never opened."""
        with self._lock:
            if self._spectra is None or self._f is None:
                return
            self._spectra.attrs["n_dropped"] = int(n_dropped)
            self._appends_since_flush = 0
            self.file.flush()
            log.info(
                "XCORR /spectra closed: %d spectra written, %d dropped",
                self.n_spectra_written, n_dropped,
            )

    def mark_finished(self, *, aborted: bool) -> None:
        """Stamp the run outcome. Called before ``__exit__`` on a controlled end."""
        with self._lock:
            self.file.attrs["aborted"] = aborted
            self.file.attrs["completed_utc"] = now_utc_iso()
            self.file.flush()
