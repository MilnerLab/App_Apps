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

        run = LoadedRun(
            path=path,
            run_id=str(_scalar(f.attrs.get("run_id", path.stem))),
            scans=scans,
            aborted=bool(_scalar(f.attrs.get("aborted", True))),
            completed_utc=str(_scalar(f.attrs.get("completed_utc", ""))),
            config=config,
        )

    log.info(
        "XCORR imported %s: %d scan(s), %d point(s), aborted=%s",
        path.name, len(run.scans), run.n_points, run.aborted,
    )
    return run
