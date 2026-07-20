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
    └─ /scans/g####_d####/  one group per (grating, delay) combination
        ├─ (attrs) grating_mm, delay_mm, delay_base_mm, delay_correction_mm,
        │          grating_index, delay_index, n_traces_per_point,
        │          utc_start, utc_end, status
        ├─ probe_mm      float64[n]
        ├─ v_mean_pos    float64[n]
        ├─ v_std         float64[n]
        └─ n_traces      int32[n]

There is deliberately no ``/plan`` table. It was a denormalised copy of what the scan
groups already carry, and it needed two workarounds — ``append_row`` cannot mutate a
row in place, and ``write_array`` throws on a zero-length array. "Which combinations
ran" is ``sorted(f["/scans"])``; "which didn't" is the planner re-run over ``/config``,
which is deterministic.
"""
from __future__ import annotations

import logging
from dataclasses import fields as dataclass_fields
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
FORMAT_VERSION = 1


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


class XcorrH5Writer:
    """Context manager owning one run file. Opened once, flushed per combination.

    The file is flushed after every ``(grating, delay)`` group and before the next
    combination is commanded (R4/§6.3), so a crash or an abort leaves a valid HDF5
    file containing every completed combination. Nothing is buffered to the end.

    ``__exit__`` always stamps ``completed_utc`` and closes, including on an
    exception — that, plus the routine's ``try/finally``, is what delivers R3.
    """

    def __init__(self, path: Path, run_id: str | None = None) -> None:
        self.path = Path(path)
        self.run_id = run_id or self.path.stem
        self._f: h5py.File | None = None
        self.n_groups_written = 0

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
        if self._f is None:
            return
        try:
            self._f.attrs["completed_utc"] = now_utc_iso()
            self._f.flush()
        finally:
            self._f.close()
            self._f = None
        log.info("XCORR run file closed: %s (%d group(s))", self.path, self.n_groups_written)

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
        g = ensure_group(self.file, "/config")
        for f in dataclass_fields(cfg):
            value = getattr(cfg, f.name)
            g.attrs[f.name] = str(value) if isinstance(value, Path) else value

        g.attrs["outer_axis"] = plan.outer_axis
        g.attrs["outer_reason"] = plan.outer_reason
        g.attrs["n_setpoints"] = len(plan.setpoints)
        g.attrs["n_probe_points"] = len(plan.probe_mm)
        g.attrs["n_points_total"] = plan.n_points
        g.attrs["plan_warnings"] = list(plan.warnings)
        self.file.flush()

    def write_provenance(self, section: str, values: dict[str, Any]) -> None:
        """Record ``/provenance/<section>`` — instrument state as found (R5).

        Provenance is read, never written: the scope's front-panel state is
        operator-owned and correct as left (§3.3.1).
        """
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
        scans = ensure_group(self.file, "/scans")
        name = setpoint.group_name
        if name in scans:
            del scans[name]
        g = scans.create_group(name)

        g.attrs["grating_mm"] = setpoint.grating_mm
        g.attrs["delay_mm"] = setpoint.delay_mm
        g.attrs["delay_base_mm"] = setpoint.delay_base_mm
        g.attrs["delay_correction_mm"] = setpoint.delay_correction_mm
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

    def mark_finished(self, *, aborted: bool) -> None:
        """Stamp the run outcome. Called before ``__exit__`` on a controlled end."""
        self.file.attrs["aborted"] = aborted
        self.file.attrs["completed_utc"] = now_utc_iso()
        self.file.flush()
