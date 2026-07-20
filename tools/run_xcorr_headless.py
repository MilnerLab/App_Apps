"""Run an XCORR scan with no window — the test backdoor described in AGENTS.md §6.2.

Everything before ``AppShell`` is headless, so this reuses the *real* boot path:
``build_context`` / ``build_container`` / ``ModuleManager.bootstrap``, the same
modules, the same DI, the same subprocesses, the same bus, real files on disk. Only
the window is missing. That is the point — a routine that can only be driven by
clicking is a routine that cannot be tested.

Three things that will bite, all of them handled below:

* **``QApplication([])`` must exist before the first ``c.get(QtDispatcher)``**, which
  happens during ``bootstrap()``. It is constructed first, as ``app.py`` does.
* **Exit on an EventBus event, not a Qt signal.** Without ``app.exec()`` there is no
  Qt event loop, so anything posted through ``QtDispatcher`` (``QTimer.singleShot``)
  never fires. The routine's own ``TaskRunner`` is unaffected.
* **Workers do not start on their own** (defect G12). The routine starts the three
  stage handles itself and waits for ``RUNNING``; nothing here flips
  ``ctx.status``, which would auto-start the spectrometer for every user.

Usage
-----
Build Step 1 acceptance run — 2 grating x 2 delay x 5 probe, acquisition stubbed::

    App_Apps\\.venv\\Scripts\\python.exe App_Apps\\tools\\run_xcorr_headless.py ^
        --grating -30 -20 10 --delay 18 19 1 --probe 74 76 0.5 --out-dir C:\\xcorr_runs

Ctrl-C requests an orderly abort; it lands at the next probe point (G15, accepted).
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

# QtDispatcher needs a QApplication, but never a window.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# app_apps is not pip-installed; it is importable by virtue of App_Apps/ being on the
# path when app.py runs. Do the same here so this works from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app_apps.routines.xcorr.config import XcorrConfig  # noqa: E402
from app_apps.routines.xcorr.events import (  # noqa: E402
    XcorrFailed,
    XcorrFinished,
    XcorrGroupWritten,
    XcorrProgress,
)
from app_apps.routines.xcorr.routine import XcorrRoutine  # noqa: E402

log = logging.getLogger("run_xcorr_headless")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--probe", nargs=3, type=float, metavar=("START", "STOP", "STEP"),
                   default=[74.0, 76.0, 0.5], help="probe (axis 1) sweep, mm")
    p.add_argument("--grating", nargs=3, type=float, metavar=("START", "STOP", "STEP"),
                   default=[-30.0, -20.0, 10.0], help="grating (axis 3) range, mm")
    p.add_argument("--delay", nargs=3, type=float, metavar=("START", "STOP", "STEP"),
                   default=[18.0, 19.0, 1.0], help="delay (axis 2) BASE range, mm")
    p.add_argument("--slope", type=float, default=0.0, help="delay correction, mm per mm of grating")
    p.add_argument("--intercept", type=float, default=0.0, help="delay correction offset, mm")
    p.add_argument("--out-dir", type=Path, default=Path.cwd() / "xcorr_runs")
    p.add_argument("--n-traces", type=int, default=10)
    p.add_argument("--settle", type=float, default=0.0, help="post-move dwell, s")
    p.add_argument("--timeout", type=float, default=130.0, help="per-command timeout, s")
    p.add_argument("--channel", type=int, default=1)
    p.add_argument("--plan-only", action="store_true",
                   help="print the plan and exit without booting anything or moving")
    return p.parse_args(argv)


def build_config(a: argparse.Namespace) -> XcorrConfig:
    return XcorrConfig(
        probe_start_mm=a.probe[0], probe_stop_mm=a.probe[1], probe_step_mm=a.probe[2],
        grating_start_mm=a.grating[0], grating_stop_mm=a.grating[1], grating_step_mm=a.grating[2],
        delay_base_start_mm=a.delay[0], delay_base_stop_mm=a.delay[1], delay_base_step_mm=a.delay[2],
        delay_slope=a.slope,
        delay_intercept_mm=a.intercept,
        out_dir=a.out_dir,
        n_traces=a.n_traces,
        settle_s=a.settle,
        timeout_s=a.timeout,
        channel=a.channel,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = build_config(args)

    if args.plan_only:
        return _plan_only(cfg)

    # Order matters: QApplication must exist before anything resolves QtDispatcher,
    # which happens inside bootstrap(). Held in a local so it is not garbage
    # collected out from under the dispatcher.
    qapp = QApplication([])  # noqa: F841

    # The real boot path, imported from app.py so this harness cannot drift from it.
    from app import build_container, build_context
    from app_apps.app.module import AppModule
    from app_apps.analysis.phase_control.module import PhaseControlModule
    from app_apps.io.control_readout.module import ControlReadoutModule
    from app_apps.io.spectrometer.module import SpectrometerModule
    from app_apps.routines.module import RoutinesModule
    from base_core.framework.modules import ModuleManager

    ctx = build_context()
    c = build_container(ctx)
    c.register_instance(XcorrConfig, cfg)  # override the module's placeholder

    modules = [
        AppModule(),
        SpectrometerModule(),
        ControlReadoutModule(),
        PhaseControlModule(),
        RoutinesModule(),
    ]
    mm = ModuleManager(modules)

    log.info("bootstrapping modules (no window)")
    mm.bootstrap(c, ctx)

    finished = threading.Event()
    outcome: dict[str, object] = {"rc": 1}
    unsubs = []

    def on_progress(e: XcorrProgress) -> None:
        log.info(
            "point %d/%d  g=%.3f d=%.3f probe=%.3f -> %.6g",
            e.points_done, e.n_points, e.grating_mm, e.delay_mm, e.probe_mm, e.v_mean_pos,
        )

    def on_group(e: XcorrGroupWritten) -> None:
        log.info("flushed /scans/%s (%d rows) — %d/%d combinations",
                 e.group_name, e.n_rows, e.setpoint_index + 1, e.n_setpoints)

    def on_finished(e: XcorrFinished) -> None:
        log.info("FINISHED%s: %d group(s) -> %s",
                 " (ABORTED)" if e.aborted else "", e.n_groups_written, e.path)
        outcome["rc"] = 0
        outcome["path"] = e.path
        finished.set()

    def on_failed(e: XcorrFailed) -> None:
        log.error("FAILED after %d group(s): %s", e.n_groups_written, e.error)
        outcome["rc"] = 3
        outcome["path"] = e.path
        finished.set()

    # Exit on a bus event, never a Qt signal: without app.exec() there is no Qt event
    # loop, so anything posted through QtDispatcher would never fire.
    unsubs.append(ctx.event_bus.subscribe(XcorrProgress, on_progress))
    unsubs.append(ctx.event_bus.subscribe(XcorrGroupWritten, on_group))
    unsubs.append(ctx.event_bus.subscribe(XcorrFinished, on_finished))
    unsubs.append(ctx.event_bus.subscribe(XcorrFailed, on_failed))

    routine: XcorrRoutine = c.get(XcorrRoutine)

    def on_sigint(_sig, _frame) -> None:
        # abort() sets a threading.Event from this thread. It must not be dispatched,
        # or it would queue behind the very loop it is meant to stop (G16).
        log.warning("interrupt — aborting at the next probe point")
        routine.abort()

    signal.signal(signal.SIGINT, on_sigint)

    try:
        log.info("starting scan; output goes to %s", cfg.out_dir)
        routine.start_scan()
        while not finished.wait(0.25):
            pass
    finally:
        for u in unsubs:
            u()
        routine.stop()
        ctx.lifecycle.add(lambda: mm.shutdown(c, ctx))
        ctx.lifecycle.clear()

    path = outcome.get("path")
    if path:
        log.info("run file: %s", path)
    return int(outcome["rc"])


def _plan_only(cfg: XcorrConfig) -> int:
    from app_apps.routines.xcorr.planner import PlanError, plan_scan

    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    try:
        plan = plan_scan(cfg)
    except PlanError as exc:
        log.error("plan rejected: %s", exc)
        return 2

    print(f"setpoints      : {len(plan.setpoints)}")
    print(f"probe points   : {len(plan.probe_mm)}")
    print(f"total points   : {plan.n_points}")
    print(f"outer axis     : {plan.outer_axis}  ({plan.outer_reason})")
    for w in plan.warnings:
        print(f"WARNING        : {w}")
    print()
    for i, s in enumerate(plan.setpoints):
        print(f"  [{i:3d}] {s.group_name}  grating={s.grating_mm:9.4f}  "
              f"delay={s.delay_mm:9.4f}  (base {s.delay_base_mm:.4f} "
              f"{s.delay_correction_mm:+.4f})")
    print(f"\n  probe: {plan.probe_mm[0]:.4f} .. {plan.probe_mm[-1]:.4f} mm "
          f"({len(plan.probe_mm)} points)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
