"""View-model for the XCORR display panel (spec R12–R15).

Owns the *scan history* of the current run and the frequency analysis, and exposes
it to the view as plain arrays + scalars. It never touches hardware. A run reaches it
by one of two doors, and both end in the same :class:`Scan` records fitted by the same
:func:`fit_sweep`, so an imported run behaves exactly like a live one.

**Door 1 — live**, reconstructed from the routine's bus events:

* ``XcorrProgress`` — one per probe point. Points are accumulated into the scan for
  their ``setpoint_index``; the newest, still-growing scan is the in-flight one.
* ``XcorrGroupWritten`` — the setpoint is complete. Its probe sweep is then fit
  **off the Qt main thread** (N1) into a :class:`FrequencyTrace`; the result is
  marshalled back and the panels redraw.

**Door 2 — imported**, read off disk by :mod:`app_apps.analysis.xcorr.run_loader`
(:meth:`XcorrDisplayViewModel.import_run`). Every scan arrives already finished, so the
whole history is fitted at once. A live run starting afterwards displaces the import —
``XcorrScanStarted`` resets the history either way, so the two can never interleave.

The fit is a pure function of ``(probe_mm, v_mean_pos)`` plus two operator display
parameters — the bandwidth window ``W`` (C23) and the grating zero ``L=0`` (C22).
Neither is stored (R15): change ``W`` and every finished scan is simply re-fit.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import dataclass, field
from functools import partial

import numpy as np
from PySide6.QtCore import QTimer, Signal

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread

from app_apps.analysis.xcorr.frequency import (
    DEFAULT_FWHM_PS,
    FrequencyTrace,
    delta_t_ps,
    fit_sweep,
    separation_mm,
)
from app_apps.analysis.xcorr.run_loader import RunLoadError, load_run
from app_apps.routines.xcorr.events import (
    XcorrFailed,
    XcorrFinished,
    XcorrGroupWritten,
    XcorrProgress,
    XcorrScanStarted,
)

log = logging.getLogger(__name__)

#: Default grating reading at zero separation (C22). Matches ``XcorrConfig.grating_zero_mm``
#: and the operator's 30.1 mm default; only shifts the summary L-axis, moves no stage.
DEFAULT_GRATING_ZERO_MM = 30.1

#: How long ``summary_changed`` is held back so a burst of landing fits redraws the
#: two grid panels once instead of once each. A 66-scan import lands 66 fits; each
#: redraw rebuilds both plots from scratch, and at ~15 Hz the grid still visibly
#: fills in while the main thread keeps most of its budget.
SUMMARY_COALESCE_MS = 66


def _fit_pool_size() -> int:
    """Fit workers to run at once.

    One fewer than the CPU count, floor 1. The fit is scipy with Python-level
    objective callbacks, so it holds the GIL for most of its ~0.64 s and this does
    NOT make a 66-scan import finish sooner. The point is the reserved core: it
    keeps the Qt main thread schedulable so the window stays responsive, instead of
    66 daemon threads starving it for ~40 s (GLOBAL_GEOMETRY §5.1).

    A process pool would sidestep the GIL but pays Windows spawn cost plus array
    pickling per task, which 0.64 s of work does not clearly cover. Measure before
    reaching for one.
    """
    return max(1, (os.cpu_count() or 2) - 1)


@dataclass
class Scan:
    """One ``(grating, delay)`` combination's probe sweep, growing live."""

    setpoint_index: int
    grating_mm: float
    delay_mm: float
    delay_base_mm: float
    probe_mm: list[float] = field(default_factory=list)
    v_mean_pos: list[float] = field(default_factory=list)
    finished: bool = False
    #: Set once the off-thread fit lands (finished scans only). ``None`` while a scan
    #: is in flight or its fit is still running or failed to produce numbers.
    trace: FrequencyTrace | None = None

    def add_point(self, probe_mm: float, v_mean_pos: float) -> None:
        self.probe_mm.append(probe_mm)
        self.v_mean_pos.append(v_mean_pos)

    @property
    def label(self) -> str:
        n = len(self.probe_mm)
        state = "done" if self.finished else f"live · {n} pt"
        return (f"g={self.grating_mm:.3f}  Δbase={self.delay_base_mm:.3f}  "
                f"({state})")


class XcorrDisplayViewModel(PanelViewModel):
    """Drives the XCORR display panel. All state lives and mutates on the Qt thread.

    The bus handlers are ``@ui_thread`` so ``_scans`` is only ever touched from the
    UI thread — the view can therefore read it directly in its slots with no lock.
    The single exception is the fit worker, which reads a *copied* snapshot of one
    finished scan's arrays (safe: a finished scan never grows again).
    """

    #: The history length or membership changed (a scan started or finished) — the
    #: view refreshes its navigation range.
    history_changed = Signal()
    #: The currently-selected scan's data changed (a new point, or its fit landed) —
    #: the view redraws the two per-scan panels.
    selection_changed = Signal()
    #: A finished scan's fit landed — the view redraws the two grid-summary panels.
    summary_changed = Signal()
    #: Overall progress or a current axis position changed — the view refreshes the
    #: position header (progress bar + per-axis bars).
    status_changed = Signal()
    #: An imported run was loaded, or the import was displaced by a live run. Carries
    #: the label to show ("" when nothing is imported).
    run_label_changed = Signal(str)
    #: ``(fits_done, fits_total)`` for the batch of fits currently outstanding, so an
    #: import or a window change shows progress instead of going silent. Both are 0
    #: when nothing is queued, which is the view's cue to hide the counter.
    fit_progress_changed = Signal(int, int)

    def __init__(self, bus: EventBus, dispatcher: QtDispatcher) -> None:
        super().__init__(bus, dispatcher)
        self._scans: list[Scan] = []
        self._by_index: dict[int, Scan] = {}
        self._selected: int = -1
        #: When True the selection tracks the newest scan as the run streams in.
        self._follow_live: bool = True
        #: Display parameters (R15) — not stored, changing them re-fits.
        self._window_ps: float = DEFAULT_FWHM_PS
        self._grating_zero_mm: float = DEFAULT_GRATING_ZERO_MM

        # Live position/progress state, fed by XcorrScanStarted (ranges) + XcorrProgress.
        self._n_points: int = 0
        self._points_done: int = 0
        self._grating_range: tuple[float, float] | None = None
        self._delay_range: tuple[float, float] | None = None
        self._probe_range: tuple[float, float] | None = None
        self._cur_grating: float | None = None
        self._cur_delay: float | None = None
        self._cur_probe: float | None = None
        #: Label of the imported run on display, "" when the panel is showing a live
        #: run (or nothing). Purely for the header — no analysis reads it.
        self._run_label: str = ""

        # Fit scheduling. The queue is the only thing crossing threads; every counter
        # below is read and written on the Qt thread alone.
        self._fit_queue: queue.Queue = queue.Queue()
        self._fit_workers: list[threading.Thread] = []
        #: Bumped whenever every outstanding fit is superseded (a window change, or a
        #: reset that threw the scans away). Workers compare it before spending 0.64 s
        #: on a result nobody will apply.
        self._fit_gen: int = 0
        self._fits_total: int = 0
        self._fits_done: int = 0

        # Redraw coalescer for summary_changed — see SUMMARY_COALESCE_MS. Started on
        # the first fit of a burst and left to run, so it fires at a bounded rate
        # rather than being pushed to the end of the burst by a restart-on-every-fit.
        self._summary_timer = QTimer(self)
        self._summary_timer.setSingleShot(True)
        self._summary_timer.setInterval(SUMMARY_COALESCE_MS)
        self._summary_timer.timeout.connect(self.summary_changed)

        self._sub(XcorrScanStarted, self._on_started)
        self._sub(XcorrProgress, self._on_progress)
        self._sub(XcorrGroupWritten, self._on_group)
        self._sub(XcorrFinished, self._on_finished)
        self._sub(XcorrFailed, self._on_failed)

    # -- read side (all called on the Qt thread) --------------------------

    @property
    def scans(self) -> list[Scan]:
        """Every scan in the current run — used for the shared (cross-scan) y-range."""
        return self._scans

    @property
    def scan_count(self) -> int:
        return len(self._scans)

    @property
    def selected_index(self) -> int:
        return self._selected

    @property
    def follow_live(self) -> bool:
        return self._follow_live

    @property
    def window_ps(self) -> float:
        return self._window_ps

    @property
    def grating_zero_mm(self) -> float:
        return self._grating_zero_mm

    def selected_scan(self) -> Scan | None:
        if 0 <= self._selected < len(self._scans):
            return self._scans[self._selected]
        return None

    def finished_scans(self) -> list[Scan]:
        """Finished scans that produced a usable fit — the grid-summary domain."""
        return [s for s in self._scans if s.finished and s.trace is not None and s.trace.ok]

    @property
    def points_done(self) -> int:
        return self._points_done

    @property
    def n_points(self) -> int:
        return self._n_points

    def axis_status(self) -> list[tuple[str, float | None, tuple[float, float] | None, str]]:
        """(label, current, (min,max), unit) for grating/delay/probe — the position
        header's rows. ``current``/range are ``None`` before a run announces itself."""
        return [
            ("Grating", self._cur_grating, self._grating_range, "mm"),
            ("Delay",   self._cur_delay,   self._delay_range,   "mm"),
            ("Probe",   self._cur_probe,   self._probe_range,   "mm"),
        ]

    # -- commands ---------------------------------------------------------

    def select(self, index: int) -> None:
        if not self._scans:
            return
        index = max(0, min(index, len(self._scans) - 1))
        # A manual selection off the newest scan turns live-follow off; picking the
        # newest again re-arms it, which is the least surprising toggle.
        self._follow_live = index == len(self._scans) - 1
        if index != self._selected:
            self._selected = index
            self.selection_changed.emit()

    def step(self, delta: int) -> None:
        self.select(self._selected + delta)

    def set_follow_live(self, on: bool) -> None:
        self._follow_live = on
        if on and self._scans:
            self.select(len(self._scans) - 1)

    @property
    def run_label(self) -> str:
        """Name of the imported run on display, or "" when live/empty."""
        return self._run_label

    def import_run(self, path) -> str:
        """Load a finished run's ``.h5`` into the panel. Returns "" or an error message.

        Called on the Qt thread from the view's file dialog. The read itself is
        synchronous — a run file is a few tens of KB and h5py reads it in well under a
        frame. The *fits* then go to the same bounded worker pool a live run uses.

        That bound is the whole point. A live run finishes one scan at a time, so
        fitting each on its own thread as it lands was harmless; an import hands over
        the entire history at once, and a thread per scan meant 66 GIL-bound scipy
        fits contending for 4 cores while the Qt main thread waited its turn. The pool
        does not make the ~42 s of work shorter — it keeps the main thread scheduled
        through it, and :attr:`fit_progress_changed` says how far along it is.

        Returns the error rather than raising: an unreadable file is an ordinary
        operator mistake (wrong file, run still in progress) and belongs in a label,
        not a traceback.
        """
        try:
            run = load_run(path)
        except RunLoadError as exc:
            return str(exc)
        except Exception as exc:                      # a corrupt file must not kill the panel
            log.exception("XCORR import failed for %s", path)
            return f"Could not read {path}: {exc}"

        self._reset()

        for ls in run.scans:
            scan = Scan(
                setpoint_index=ls.setpoint_index,
                grating_mm=ls.grating_mm,
                delay_mm=ls.delay_mm,
                delay_base_mm=ls.delay_base_mm,
                probe_mm=list(ls.probe_mm),
                v_mean_pos=list(ls.v_mean_pos),
                finished=True,
            )
            self._by_index[ls.setpoint_index] = scan
            self._scans.append(scan)

        # Header state: an imported run is 100% done by construction, so points_done
        # is what is actually on disk — not the plan's total, which counts points a
        # run that aborted early never took.
        self._n_points = run.n_points
        self._points_done = run.n_points
        self._grating_range = run.grating_range_mm
        self._delay_range = run.delay_base_range_mm
        self._probe_range = run.probe_base_range_mm if run.n_points else None
        # No live axis positions for a file — the header shows ranges, not a cursor.
        self._cur_grating = self._cur_delay = self._cur_probe = None

        self._selected = len(self._scans) - 1
        self._follow_live = True

        n_empty = sum(1 for s in run.scans if not s.probe_mm.size)
        self._run_label = (
            f"{run.path.name} — {len(run.scans)} scan(s), {run.n_points} pt"
            + (f", {n_empty} empty" if n_empty else "")
            + (" (ABORTED)" if run.aborted else "")
        )

        self.history_changed.emit()
        self.status_changed.emit()
        self.selection_changed.emit()
        self.run_label_changed.emit(self._run_label)

        for scan in self._scans:
            if scan.probe_mm:
                self._launch_fit(scan)
        return ""

    def set_window_ps(self, value: float) -> None:
        """Change the bandwidth readout window W (C23) and re-fit every finished scan."""
        if value <= 0 or value == self._window_ps:
            return
        self._window_ps = float(value)
        # Every queued fit carries the old window, so retire the batch before queuing
        # the new one — otherwise a second window change mid-re-fit leaves the counter
        # totalling two runs' worth of work that will never all be applied.
        self._supersede_fits()
        for scan in self._scans:
            if scan.finished:
                self._launch_fit(scan)

    def set_grating_zero_mm(self, value: float) -> None:
        """Change the grating zero L=0 (C22). Only shifts the summary L-axis — no re-fit."""
        if value == self._grating_zero_mm:
            return
        self._grating_zero_mm = float(value)
        self.summary_changed.emit()

    # summary coordinates, in the run's own units --------------------------

    def delta_t_ps(self, scan: Scan) -> float:
        return delta_t_ps(scan.delay_base_mm)

    def separation_mm(self, scan: Scan) -> float:
        return separation_mm(scan.grating_mm, self._grating_zero_mm)

    # -- bus handlers (marshalled to the Qt thread) -----------------------

    @ui_thread
    def _on_started(self, e: XcorrScanStarted) -> None:
        # New run: clear the previous run's history and load this run's ranges/totals.
        self._reset()
        self._grating_range = e.grating_range_mm
        self._delay_range = e.delay_base_range_mm
        self._probe_range = e.probe_base_range_mm
        self._n_points = e.n_points
        self._points_done = 0
        self._cur_grating = self._cur_delay = self._cur_probe = None
        self.status_changed.emit()

    @ui_thread
    def _on_progress(self, e: XcorrProgress) -> None:
        # points_done == 1 is the first point of a whole run — start a fresh history so
        # a second run in the same session does not append to the first. Normally
        # XcorrScanStarted has already reset (and cleared _scans), so this is only a
        # fallback for a run that streamed progress without a start event.
        if e.points_done == 1 and self._scans:
            self._reset()

        self._points_done = e.points_done
        self._n_points = e.n_points
        self._cur_grating = e.grating_mm
        self._cur_delay = e.delay_base_mm
        self._cur_probe = e.probe_base_mm
        self.status_changed.emit()

        scan = self._by_index.get(e.setpoint_index)
        if scan is None:
            scan = Scan(
                setpoint_index=e.setpoint_index,
                grating_mm=e.grating_mm,
                delay_mm=e.delay_mm,
                delay_base_mm=e.delay_base_mm,
            )
            self._by_index[e.setpoint_index] = scan
            self._scans.append(scan)
            if self._follow_live:
                self._selected = len(self._scans) - 1
            self.history_changed.emit()

        scan.add_point(e.probe_mm, e.v_mean_pos)
        if scan is self.selected_scan():
            self.selection_changed.emit()

    @ui_thread
    def _on_group(self, e: XcorrGroupWritten) -> None:
        scan = self._by_index.get(e.setpoint_index)
        if scan is None:
            return
        scan.finished = True
        self.history_changed.emit()
        self._launch_fit(scan)

    @ui_thread
    def _on_finished(self, _e: XcorrFinished) -> None:
        # Nothing to tear down — the history stays on screen for review after the run.
        self.history_changed.emit()

    @ui_thread
    def _on_failed(self, _e: XcorrFailed) -> None:
        self.history_changed.emit()

    # -- fitting (off the Qt main thread, N1) -----------------------------

    def _launch_fit(self, scan: Scan) -> None:
        """Queue one scan's fit. Called on the Qt thread; returns immediately."""
        # Snapshot the arrays so the worker never races a still-mutating list. A
        # finished scan does not grow, but a re-fit (window change) can run while the
        # next scan streams in, so copying is the simple correctness guarantee.
        probe = np.asarray(scan.probe_mm, dtype=float)
        vmean = np.asarray(scan.v_mean_pos, dtype=float)
        self._start_fit_workers()
        self._fits_total += 1
        self._fit_queue.put(
            (self._fit_gen, scan.setpoint_index, probe, vmean, self._window_ps))
        self.fit_progress_changed.emit(self._fits_done, self._fits_total)

    def _start_fit_workers(self) -> None:
        # Started on first use rather than in __init__ so a panel that never sees a
        # scan never spawns threads. They live for the panel's lifetime: the pool is
        # small and idles blocked on the queue, so there is nothing to tear down.
        if self._fit_workers:
            return
        for i in range(_fit_pool_size()):
            th = threading.Thread(target=self._fit_worker, name=f"xcorr-fit-{i}",
                                  daemon=True)
            self._fit_workers.append(th)
            th.start()

    def _fit_worker(self) -> None:
        while True:
            gen, idx, probe, vmean, window = self._fit_queue.get()
            trace = None
            try:
                # Reading _fit_gen unsynchronised is safe and deliberate: it is an int
                # rebind, and a stale read only costs one wasted fit whose result
                # _apply_fit would reject anyway.
                if gen == self._fit_gen:
                    trace = fit_sweep(probe, vmean, fwhm_ps=window)
            except Exception:  # frequency.fit_sweep is total, but never let a fit crash the panel
                log.exception("XCORR fit crashed for setpoint %d", idx)
            # Posted even when the fit was skipped or crashed, so the progress counter
            # always drains — a stalled counter would read as a hung import.
            self._post(partial(self._fit_finished, idx, window, trace))

    def _fit_finished(self, setpoint_index: int, window_ps: float,
                      trace: FrequencyTrace | None) -> None:
        # Back on the Qt thread. Account for the fit first, apply it second: a skipped
        # or crashed fit still counts as retired work.
        self._fits_done += 1
        if self._fits_done >= self._fits_total:
            self._fits_total = self._fits_done = 0     # batch drained — hide the counter
        self.fit_progress_changed.emit(self._fits_done, self._fits_total)
        if trace is not None:
            self._apply_fit(setpoint_index, window_ps, trace)

    def _apply_fit(self, setpoint_index: int, window_ps: float, trace: FrequencyTrace) -> None:
        # Drop the result if the window changed again while this fit was in flight —
        # a newer re-fit is already queued behind it. The generation check in the
        # worker usually catches this first; this is the check that has to be right.
        if window_ps != self._window_ps:
            return
        scan = self._by_index.get(setpoint_index)
        if scan is None:
            return
        scan.trace = trace
        if scan is self.selected_scan():
            self.selection_changed.emit()
        if not self._summary_timer.isActive():
            self._summary_timer.start()

    def _supersede_fits(self) -> None:
        """Invalidate every outstanding fit. Callers re-queue what they still want."""
        self._fit_gen += 1

    # -- plumbing ---------------------------------------------------------

    def _reset(self) -> None:
        # The scans these fits belong to are about to stop existing.
        self._supersede_fits()
        self._scans.clear()
        self._by_index.clear()
        self._selected = -1
        self._follow_live = True
        # Any reset displaces an import: either a live run is starting (XcorrScanStarted)
        # or another file is being loaded, and in both cases the old label is now a lie.
        # import_run() re-sets it immediately afterwards.
        if self._run_label:
            self._run_label = ""
            self.run_label_changed.emit("")
        self.history_changed.emit()
