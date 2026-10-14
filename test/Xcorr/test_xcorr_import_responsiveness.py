"""Acceptance check for the XCORR import freeze (GLOBAL_GEOMETRY §5).

The bug was never wrong *numbers* — it was scheduling. A 66-setpoint import spawned
66 daemon threads of GIL-bound scipy, the Qt main thread stopped being scheduled, and
Windows marked the window "Not Responding" for most of ~40 s. So the thing to measure
is main-thread latency during a real import, not the fit output.

Method: a QTimer fires every ``TICK_MS``; the gap between consecutive ticks is how
long the main thread was unable to run. On the old code that gap reached seconds. The
test drives the real view-model against a real run file — a synthetic trace would not
reproduce the load that caused the problem.

Also asserts that the progress signal actually advances, since a counter that never
moves is indistinguishable to an operator from the freeze it was added to explain.

Run it directly; needs a display-less QApplication only (no window is shown):
    .venv/Scripts/python.exe test/test_xcorr_import_responsiveness.py [run.h5]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer                                   # noqa: E402
from PySide6.QtWidgets import QApplication                          # noqa: E402

from base_core.framework.events import EventBus                     # noqa: E402
from base_qt.app.dispatcher import QtDispatcher                     # noqa: E402

from app_apps.analysis.xcorr.ui.xcorr_display_view_model import (   # noqa: E402
    XcorrDisplayViewModel,
)

DEFAULT_RUN = "xcorr_runs/XCORR_20260725_142505.h5"

#: Second readout window, used to drive the re-fit path (``set_window_ps``), which has
#: the identical unbounded-threading problem and the same progress gap. Any value that
#: differs from the default will do.
REFIT_WINDOW_PS = 200.0

#: Main-thread sampling period. Small enough that a stall of operator-visible length
#: cannot hide between two ticks.
TICK_MS = 50
#: Worst tolerated gap between ticks once the fits are running. This is what the fix
#: is actually about; measured on this machine it is ~0.2 s, against 33.8 s before the
#: change. Windows shows "Not Responding" at ~5 s, so 3 s leaves margin on a busier
#: host while still failing loudly if unbounded threading ever comes back.
MAX_STALL_S = 3.0
#: The h5py read plus building the Scan records runs synchronously on the Qt thread,
#: by design, and is charged separately: it is bounded by file size (~0.3-1.7 s here,
#: noisy) and no amount of fit scheduling changes it. Contradicts import_run's old
#: claim that the read lands "well under a frame" — see the write-up.
MAX_SYNC_LOAD_S = 4.0
#: Give up rather than hang if the import never drains.
TIMEOUT_S = 300.0


class _Probe:
    """Samples main-thread latency and the fit-progress signal during an import."""

    def __init__(self, vm: XcorrDisplayViewModel) -> None:
        self.vm = vm
        self.gaps: list[float] = []
        self.progress: list[tuple[int, int]] = []
        self.done = False
        self._last = time.perf_counter()
        self._t0 = self._last
        vm.fit_progress_changed.connect(self._on_progress)
        self._timer = QTimer()
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.append((done, total))
        # (0, 0) is the view-model's "batch drained" signal — the import is analysed.
        if self.progress and (done, total) == (0, 0) and len(self.progress) > 1:
            self.done = True

    def _tick(self) -> None:
        now = time.perf_counter()
        self.gaps.append(now - self._last)
        self._last = now
        if self.done or now - self._t0 > TIMEOUT_S:
            self._timer.stop()
            QApplication.instance().quit()

    @property
    def elapsed_s(self) -> float:
        return self._last - self._t0


def _readout(vm: XcorrDisplayViewModel) -> dict[int, tuple[float, float]]:
    """``{setpoint_index: (f0, bandwidth)}`` for every scan currently carrying a fit."""
    return {s.setpoint_index: (s.trace.f_central_ghz, s.trace.bandwidth_ghz)
            for s in vm.finished_scans()}


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else DEFAULT_RUN
    if not Path(path).is_file():
        print(f"SKIP: no run file at {path}")
        return 0

    app = QApplication.instance() or QApplication([])
    vm = XcorrDisplayViewModel(EventBus(), QtDispatcher(app))

    t_sync = time.perf_counter()
    error = vm.import_run(path)
    sync_s = time.perf_counter() - t_sync
    if error:
        print(f"FAIL: import returned {error!r}")
        return 1
    # The probe starts *after* the synchronous load so the two phases are scored
    # separately — otherwise the read time hides inside the scheduling number the
    # fix is meant to move.
    probe = _Probe(vm)
    app.exec()

    n_scans = vm.scan_count
    worst = max(probe.gaps) if probe.gaps else float("inf")
    fitted = len(vm.finished_scans())
    peak_total = max((t for _d, t in probe.progress), default=0)
    import_values = _readout(vm)
    print(f"scans={n_scans}  fitted_ok={fitted}  elapsed={probe.elapsed_s:.1f} s")
    print(f"synchronous load+build={sync_s:.2f} s (read, not scheduling)")
    print(f"main-thread ticks={len(probe.gaps)}  worst gap={worst * 1e3:.0f} ms "
          f"(tick interval {TICK_MS} ms)")
    print(f"progress emissions={len(probe.progress)}  peak total={peak_total}")

    # --- the re-fit path (window change), which had the same defect ------------
    refit = _Probe(vm)
    vm.set_window_ps(REFIT_WINDOW_PS)
    app.exec()
    refit_worst = max(refit.gaps) if refit.gaps else float("inf")
    refit_values = _readout(vm)
    print(f"re-fit at W={REFIT_WINDOW_PS} ps: worst gap={refit_worst * 1e3:.0f} ms  "
          f"fitted_ok={len(vm.finished_scans())}  "
          f"peak total={max((t for _d, t in refit.progress), default=0)}")

    failures = []
    # Every scan that fitted on import must still carry a fit afterwards, and the
    # readout must actually have moved: a generation check that is too aggressive
    # would silently drop results, and one that is absent would leave stale ones.
    stale = [i for i in import_values if i not in refit_values]
    if stale:
        failures.append(f"{len(stale)} scan(s) lost their fit across the window change")
    unchanged = [i for i in import_values
                 if i in refit_values and refit_values[i] == import_values[i]]
    if len(unchanged) > 0.2 * max(1, len(import_values)):
        failures.append(f"{len(unchanged)}/{len(import_values)} readouts did not change "
                        f"with W — the re-fit did not land")
    if not refit.done:
        failures.append("the window-change re-fit never drained")
    if refit_worst > MAX_STALL_S:
        failures.append(f"main thread stalled {refit_worst:.2f} s during the re-fit")
    if not probe.done:
        failures.append(f"import did not drain within {TIMEOUT_S:.0f} s")
    if worst > MAX_STALL_S:
        failures.append(f"main thread stalled {worst:.2f} s > {MAX_STALL_S} s during fits")
    if sync_s > MAX_SYNC_LOAD_S:
        failures.append(f"synchronous load took {sync_s:.2f} s > {MAX_SYNC_LOAD_S} s")
    if peak_total < n_scans:
        failures.append(f"progress peaked at {peak_total}, expected >= {n_scans}")
    if len({d for d, _t in probe.progress}) < 3:
        failures.append("progress counter never visibly advanced")
    if fitted == 0:
        failures.append("no scan produced a usable fit")

    for f in failures:
        print(f"FAIL: {f}")
    if not failures:
        print("all passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
