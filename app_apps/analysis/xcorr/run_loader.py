"""Read a finished XCORR run back off disk into the display panel's own records.

The display panel reconstructs a run from the routine's bus events, which only exist
while a scan is running. This module is the other door: it turns an ``.h5`` written by
:mod:`app_apps.routines.xcorr.storage` back into the same shape, so an old run can be
navigated and re-fit exactly like a live one. Nothing here is analysis — the fit is
still :func:`app_apps.analysis.xcorr.frequency.fit_sweep`, run by the view-model on its
own thread, so an imported run and a live one go through identical code.

Pure numpy/h5py: no Qt. Every entry point either returns a :class:`LoadedRun` or raises
:class:`RunLoadError` with a message fit to show an operator.

**Execution order is reconstructed, not assumed.** Group names sort in *grid* order
(``g####_d####``), which is only execution order when the grating took the outer loop.
``/config`` records ``outer_axis`` for exactly this reason, so the scans come back
numbered the way they were actually taken.

**A live run's file is locked.** The writer holds the HDF5 file open for the whole run
(Windows denies the shared read), so importing the run that is currently in progress
fails with a clear message rather than a raw ``OSError``. Snapshotting a locked file is
deliberately not attempted: a mid-flush copy is not a valid HDF5 file.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import h5py
import numpy as np

log = logging.getLogger(__name__)

#: Format written by ``storage.XcorrH5Writer``. Anything else is refused up front —
#: a wrong-schema file would otherwise fail deep inside with a KeyError.
FORMAT_NAME = "milnerlab-xcorr"


class RunLoadError(RuntimeError):
    """The file is missing, locked, not an XCORR run, or carries no scan groups."""


@dataclass(frozen=True)
class LoadedScan:
    """One ``(grating, delay)`` group, in the fields :class:`Scan` needs.

    ``probe_mm`` holds the *commanded* positions, as written. The grating-independent
    base axis is ``probe_mm - probe_offset_mm``; the offset is carried so a caller can
    recover it without re-deriving the planner's geometry.
    """

    setpoint_index: int
    group_name: str
    grating_mm: float
    delay_mm: float
    delay_base_mm: float
    probe_offset_mm: float
    probe_mm: np.ndarray
    v_mean_pos: np.ndarray
    v_std: np.ndarray
    n_traces: np.ndarray
    status: str
    utc_start: str = ""
    utc_end: str = ""

    @property
    def n_points(self) -> int:
        return int(self.probe_mm.size)

    @property
    def probe_base_mm(self) -> np.ndarray:
        return self.probe_mm - self.probe_offset_mm


@dataclass(frozen=True)
class LoadedRun:
    """A whole run: its scans in execution order, plus what the header panel shows."""

    path: Path
    run_id: str
    scans: list[LoadedScan]
    aborted: bool
    completed_utc: str
    #: One entry per ``/config`` attribute, for provenance display. Values are whatever
    #: h5py hands back (numpy scalars are converted to Python for cleanliness).
    config: dict = field(default_factory=dict)
    #: ``/spectra`` attributes plus ``n_rows``, or empty when the run recorded no
    #: spectra. Deliberately *not* the spectra themselves — a long run's ``counts`` is
    #: hundreds of MB and ``load_run`` is on the panel's import path. Call
    #: :func:`load_spectra` for the arrays.
    spectra_meta: dict = field(default_factory=dict)

    @property
    def has_spectra(self) -> bool:
        return bool(self.spectra_meta.get("n_rows", 0))

    @property
    def n_points(self) -> int:
        """Total probe points actually on disk — what the progress bar should show as
        complete, not ``config['n_points_total']`` (which is what was *planned*)."""
        return sum(s.n_points for s in self.scans)

    @property
    def grating_range_mm(self) -> tuple[float, float]:
        v = [s.grating_mm for s in self.scans]
        return (min(v), max(v))

    @property
    def delay_base_range_mm(self) -> tuple[float, float]:
        v = [s.delay_base_mm for s in self.scans]
        return (min(v), max(v))

    @property
    def probe_base_range_mm(self) -> tuple[float, float]:
        lo = min(float(s.probe_base_mm.min()) for s in self.scans if s.n_points)
        hi = max(float(s.probe_base_mm.max()) for s in self.scans if s.n_points)
        return (lo, hi)


def _scalar(v):
    """h5py attr → plain Python. Keeps bytes/numpy out of the UI layer."""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return [_scalar(x) for x in v]
    return v


def _open(path: Path) -> h5py.File:
    """Open read-only, translating the two failure modes an operator will actually hit."""
    if not path.exists():
        raise RunLoadError(f"No such file: {path}")
    try:
        return h5py.File(path, "r")
    except OSError as exc:
        # HDF5 reports a Windows sharing violation as a lock failure. The only thing
        # that holds an XCORR file open is the writer, so this is the in-progress run.
        if "unable to lock file" in str(exc) or "errno = 33" in str(exc):
            raise RunLoadError(
                f"{path.name} is locked — the run writing it is still in progress. "
                f"Import it once the scan finishes."
            ) from exc
        raise RunLoadError(f"Could not open {path.name}: {exc}") from exc


def load_run(path: str | Path) -> LoadedRun:
    """Read one XCORR ``.h5`` into a :class:`LoadedRun`.

    Empty groups (a combination that was skipped, or aborted before its first probe
    point) are kept, not dropped: they are how the file records that the combination
    was reached, and the panel shows them as an empty scan rather than a silent gap.
    """
    path = Path(path)
    with _open(path) as f:
        fmt = _scalar(f.attrs.get("format_name", ""))
        if fmt != FORMAT_NAME:
            raise RunLoadError(
                f"{path.name} is not an XCORR run file "
                f"(format_name={fmt!r}, expected {FORMAT_NAME!r})."
            )
        if "/scans" not in f:
            raise RunLoadError(f"{path.name} contains no /scans group.")

        config = {k: _scalar(v) for k, v in f["/config"].attrs.items()} if "/config" in f else {}
        outer = str(config.get("outer_axis", "grating"))

        raw = []
        scans_g = f["/scans"]
        for name in scans_g:
            g = scans_g[name]
            a = g.attrs
            gi = int(_scalar(a.get("grating_index", 0)))
            di = int(_scalar(a.get("delay_index", 0)))
            # Execution order: the outer loop's index is the major sort key. Group
            # names alone sort by grating first, which is wrong when delay was outer.
            key = (gi, di) if outer == "grating" else (di, gi)
            raw.append((key, name, g))

        if not raw:
            raise RunLoadError(f"{path.name} contains no scan groups — the run wrote nothing.")

        raw.sort(key=lambda r: r[0])

        scans: list[LoadedScan] = []
        for idx, (_key, name, g) in enumerate(raw):
            a = g.attrs

            def arr(key: str, dtype=float) -> np.ndarray:
                if key not in g:
                    return np.empty(0, dtype=dtype)
                return np.asarray(g[key][()], dtype=dtype)

            scans.append(LoadedScan(
                setpoint_index=idx,
                group_name=str(name),
                grating_mm=float(_scalar(a.get("grating_mm", np.nan))),
                delay_mm=float(_scalar(a.get("delay_mm", np.nan))),
                delay_base_mm=float(_scalar(a.get("delay_base_mm", np.nan))),
                probe_offset_mm=float(_scalar(a.get("probe_offset_mm", 0.0))),
                probe_mm=arr("probe_mm"),
                v_mean_pos=arr("v_mean_pos"),
                v_std=arr("v_std"),
                n_traces=arr("n_traces", np.int32),
                status=str(_scalar(a.get("status", ""))),
                utc_start=str(_scalar(a.get("utc_start", ""))),
                utc_end=str(_scalar(a.get("utc_end", ""))),
            ))

        # Absent in format_version 1 and in any run where the spectrometer was not
        # recording, so this stays an empty dict rather than a failure.
        spectra_meta: dict = {}
        if "/spectra" in f:
            sg = f["/spectra"]
            spectra_meta = {k: _scalar(v) for k, v in sg.attrs.items()}
            spectra_meta["n_rows"] = int(sg["counts"].shape[0]) if "counts" in sg else 0

        run = LoadedRun(
            path=path,
            run_id=str(_scalar(f.attrs.get("run_id", path.stem))),
            scans=scans,
            aborted=bool(_scalar(f.attrs.get("aborted", True))),
            completed_utc=str(_scalar(f.attrs.get("completed_utc", ""))),
            config=config,
            spectra_meta=spectra_meta,
        )

    log.info(
        "XCORR imported %s: %d scan(s), %d point(s), %d spectrum/spectra, aborted=%s",
        path.name, len(run.scans), run.n_points, run.spectra_meta.get("n_rows", 0), run.aborted,
    )
    return run


@dataclass(frozen=True)
class LoadedSpectra:
    """The ``/spectra`` stream: one row per spectrum, plus the shared wavelength axis.

    Rows are **not** aligned with any scan's ``probe_mm``. The spectrometer free-runs, so
    a probe point carries however many spectra happened to complete while the stages were
    stationary — sometimes none. Join on ``setpoint_index``/``probe_index`` (or on the
    recorded positions), never on row index; :meth:`for_setpoint` does the former.
    """

    wavelength_nm: np.ndarray
    counts: np.ndarray          # (N, n_pixels)
    timestamp_ns: np.ndarray
    setpoint_index: np.ndarray
    probe_index: np.ndarray
    grating_mm: np.ndarray
    delay_mm: np.ndarray
    probe_mm: np.ndarray
    attrs: dict = field(default_factory=dict)

    @property
    def n_rows(self) -> int:
        return int(self.counts.shape[0])

    def for_setpoint(self, setpoint_index: int) -> np.ndarray:
        """Row indices belonging to one ``(grating, delay)`` combination."""
        return np.flatnonzero(self.setpoint_index == setpoint_index)


def load_spectra(path: str | Path, *, setpoint_index: int | None = None) -> LoadedSpectra:
    """Read ``/spectra`` from an XCORR run.

    Separate from :func:`load_run` because ``counts`` is the one array in the file big
    enough to matter: a two-hour run is ~100 k rows × 3648 float32, so the panel asks for
    it only when someone actually wants to look at spectra. Pass ``setpoint_index`` to
    read just one combination's rows — the index columns are small, so this reads them
    first and slices ``counts`` rather than materialising the whole thing.
    """
    path = Path(path)
    with _open(path) as f:
        if "/spectra" not in f:
            raise RunLoadError(
                f"{path.name} contains no /spectra group — no spectrometer was recording "
                f"during this run."
            )
        g = f["/spectra"]
        attrs = {k: _scalar(v) for k, v in g.attrs.items()}

        def col(name: str, dtype) -> np.ndarray:
            if name not in g:
                return np.empty(0, dtype=dtype)
            return np.asarray(g[name][()], dtype=dtype)

        setpoints = col("setpoint_index", np.int32)
        if setpoint_index is None:
            rows = slice(None)
            counts = np.asarray(g["counts"][()], dtype=np.float32)
        else:
            # h5py fancy-indexes only with a sorted, unique list — flatnonzero gives
            # exactly that, and an empty selection has to be special-cased.
            sel = np.flatnonzero(setpoints == int(setpoint_index))
            rows = sel
            n_pixels = int(g["counts"].shape[1])
            counts = (
                np.asarray(g["counts"][sel, :], dtype=np.float32)
                if sel.size
                else np.empty((0, n_pixels), dtype=np.float32)
            )

        return LoadedSpectra(
            wavelength_nm=col("wavelength_nm", np.float64),
            counts=counts,
            timestamp_ns=col("timestamp_ns", np.int64)[rows],
            setpoint_index=setpoints[rows],
            probe_index=col("probe_index", np.int32)[rows],
            grating_mm=col("grating_mm", np.float64)[rows],
            delay_mm=col("delay_mm", np.float64)[rows],
            probe_mm=col("probe_mm", np.float64)[rows],
            attrs=attrs,
        )
