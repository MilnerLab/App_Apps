"""Start the slow (frozen-template) stabilization loop with no window, and report why.

The panel can only show a capture counter. When that counter sits at 0/10 the operator
cannot tell which of three things is happening: every trace is being rejected before the
fit, the fit is running and failing its accept gate, or the run is completing and being
abandoned on the averaged trace. This boots the REAL application -- same modules, same
DI, same subprocesses, same spectrometer -- minus the window, and prints the answer.

Per frame it reports the fringe-contrast index against the gate that admits it, so a
stall reads as a number and a threshold rather than as a stuck counter.

    App_Apps\.venv\Scripts\python.exe tools/run_stabilization_headless.py --seconds 60

Nothing is moved: the RGV correction path is left connected, so this observes exactly the
loop the operator runs. Pass --no-correct to keep the plate still while watching.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

log = logging.getLogger("stabilization_headless")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=60.0, help="how long to watch")
    ap.add_argument("--mismatch-max", type=float, default=None,
                    help="override shape_mismatch_max, to see how much headroom the "
                         "live traces actually need before the template survives")
    ap.add_argument("--no-correct", action="store_true",
                    help="observe only: swallow corrections so the plate never turns")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    qapp = QApplication([])  # noqa: F841  -- must exist before QtDispatcher resolves

    from app import build_container, build_context
    from app_apps.analysis.phase_control.events import PhaseTemplateChanged
    from app_apps.analysis.phase_control.module import PhaseControlModule
    from app_apps.analysis.phase_control.phase_stabilization_handle import (
        PhaseStabilizationHandle,
    )
    from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
        StabilizationConfig,
    )
    from app_apps.app.module import AppModule
    from app_apps.io.control_readout.module import ControlReadoutModule
    from app_apps.io.spectrometer.events import SpectrumAvailable
    from app_apps.io.spectrometer.module import SpectrometerModule
    from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle
    from base_core.framework.modules import ModuleManager
    from base_core.ipc.worker_handle import WorkerStatus

    ctx = build_context()
    c = build_container(ctx)
    modules = [AppModule(), SpectrometerModule(), ControlReadoutModule(), PhaseControlModule()]
    mm = ModuleManager(modules)

    unsubs: list = []
    seen = {"frames": 0, "last": None}

    def on_template(e: PhaseTemplateChanged) -> None:
        # This is the event the panel's counter renders. Printing it here proves whether
        # the counter is standing still because the loop is, or because nothing is sent.
        stamp = (e.state, e.captured)
        if stamp == seen["last"]:
            return
        seen["last"] = stamp
        log.warning("TEMPLATE STATE -> %s  %d/%d", e.state, e.captured, e.needed)

    def on_spectrum(_e: SpectrumAvailable) -> None:
        seen["frames"] += 1

    try:
        log.info("bootstrapping modules (no window)")
        mm.bootstrap(c, ctx)
        unsubs.append(ctx.event_bus.subscribe(PhaseTemplateChanged, on_template))
        unsubs.append(ctx.event_bus.subscribe(SpectrumAvailable, on_spectrum))

        cfg = c.get(StabilizationConfig)
        cfg.slow_correction = True
        if args.mismatch_max is not None:
            cfg.shape_mismatch_max = float(args.mismatch_max)
        log.warning("slow_correction=%s  min_visibility=%.3f  rms_frac<%.2f  inliers>%.0f%%"
                    "  correction period %.1fs",
                    cfg.slow_correction, cfg.min_visibility, cfg.rms_frac_threshold,
                    cfg.inlier_threshold, cfg.correction_period_s)
        log.warning("shape_mismatch_max=%.4f", cfg.shape_mismatch_max)

        spectro = c.get(SpectrometerWorkerHandle)
        phase = c.get(PhaseStabilizationHandle)

        if args.no_correct:
            from app_apps.io.control_readout.rgv.events import RequestRotateRGV
            unsubs.append(ctx.event_bus.subscribe(
                RequestRotateRGV,
                lambda e: log.warning("correction %.4f deg SWALLOWED (--no-correct)",
                                      e.angle.Deg)))

        log.info("starting the spectrometer")
        spectro.start()
        deadline = time.time() + 20.0
        while spectro.state != WorkerStatus.RUNNING and time.time() < deadline:
            time.sleep(0.1)
        if spectro.state != WorkerStatus.RUNNING:
            log.error("the spectrometer never reached RUNNING (state=%s). "
                      "Without spectra there is nothing for the loop to capture.",
                      spectro.state)
            return 2

        log.info("starting the phase loop")
        phase.start()
        # The subprocess was handed its config at construction, so mutating the shared
        # object here is not enough -- it has to be pushed over the wire, and only after
        # start(), since _start() rebuilds the tracker from whatever it holds.
        time.sleep(0.5)
        phase.set_config(cfg)

        end = time.time() + args.seconds
        while time.time() < end:
            time.sleep(1.0)

        log.warning("watched %.0f s: %d spectra published, final template state %s",
                    args.seconds, seen["frames"], seen["last"])
        return 0
    finally:
        for u in unsubs:
            try:
                u()
            except Exception:
                pass
        log.info("shutting down")
        mm.shutdown(c, ctx)


if __name__ == "__main__":
    sys.exit(main())
