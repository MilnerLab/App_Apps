"""Start the stabilization loop with no window, and report why.

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
    ap.add_argument("--capture-n", type=int, default=None,
                    help="override capture_n, to see how many consecutively accepted "
                         "traces the live signal can actually string together")
    ap.add_argument("--window", type=float, nargs=2, metavar=("LO", "HI"), default=None,
                    help="analysis window in nm, e.g. --window 800 804. This is the same "
                         "knob the panel calls the wavelength range; narrowing it is what "
                         "'zooming in' means to the fit.")
    ap.add_argument("--avg", type=int, default=None, help="override avg_spectra")
    ap.add_argument("--invert", dest="invert", action="store_true", default=None,
                    help="force invert_correction on")
    ap.add_argument("--integration-ms", type=float, default=None,
                    help="override the spectrometer integration time (ms)")
    ap.add_argument("--averages", type=int, default=None,
                    help="override the spectrometer hardware average count")
    ap.add_argument("--roi", type=float, nargs=2, metavar=("LO", "HI"), default=None,
                    help="analysis ROI in nm, e.g. --roi 800 804. UNLIKE --window this is "
                         "the operator override: the fit uses exactly this region, the "
                         "envelopes stay on 790-814, and the crop / end-trim / truncation "
                         "detector / recovery scan and the trust+residual+inlier gates are "
                         "all off. Omit it for today's fully automatic behaviour.")
    ap.add_argument("--no-correct", action="store_true",
                    help="observe only: swallow corrections so the plate never turns")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    qapp = QApplication([])  # noqa: F841  -- must exist before QtDispatcher resolves

    from app import build_container, build_context
    from app_apps.analysis.phase_control.events import PhaseBatchChanged
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

    def on_batch(e: PhaseBatchChanged) -> None:
        # This is the event the panel's counter renders. Printing it here proves whether
        # the counter is standing still because the loop is, or because nothing is sent.
        stamp = (e.capturing, e.collected, e.settling)
        if stamp == seen["last"]:
            return
        seen["last"] = stamp
        log.warning("%s -> %d/%d%s", "CAPTURING" if e.capturing else "AVERAGING",
                    e.collected, e.needed, "  (settling)" if e.settling else "")

    def on_spectrum(_e: SpectrumAvailable) -> None:
        seen["frames"] += 1

    try:
        log.info("bootstrapping modules (no window)")
        mm.bootstrap(c, ctx)
        unsubs.append(ctx.event_bus.subscribe(PhaseBatchChanged, on_batch))
        unsubs.append(ctx.event_bus.subscribe(SpectrumAvailable, on_spectrum))

        cfg = c.get(StabilizationConfig)
        if args.capture_n is not None:
            cfg.capture_n = int(args.capture_n)
        if args.avg is not None:
            cfg.avg_spectra = int(args.avg)
        if args.invert is not None:
            cfg.invert_correction = bool(args.invert)
        if args.roi is not None:
            cfg.roi_lo, cfg.roi_hi = float(args.roi[0]), float(args.roi[1])
        if args.window is not None:
            from base_core.math.models import Range
            from base_core.quantities.enums import Prefix as _P
            from base_core.quantities.models import Length
            lo, hi = args.window
            cfg.wavelength_range = Range(Length(lo, _P.NANO), Length(hi, _P.NANO))
        from base_core.quantities.enums import Prefix as _P2
        log.warning("window %.2f-%.2f nm  lambda_ref=%.2f nm",
                    cfg.wavelength_range.min.value(_P2.NANO),
                    cfg.wavelength_range.max.value(_P2.NANO),
                    cfg.params.lambda_ref.value(_P2.NANO))
        log.warning("min_visibility=%.3f  rms_frac<%.2f  inliers>%.0f%%"
                    "  %d frames per correction  deadband %.1f deg",
                    cfg.min_visibility, cfg.rms_frac_threshold,
                    cfg.inlier_threshold, cfg.avg_spectra, cfg.phase_tolerance.Deg)
        log.warning("capture_n=%d  move_settle_s=%.2f", cfg.capture_n, cfg.move_settle_s)

        spectro = c.get(SpectrometerWorkerHandle)
        phase = c.get(PhaseStabilizationHandle)

        if args.no_correct:
            from app_apps.io.control_readout.rgv.events import RequestRotateRGV
            unsubs.append(ctx.event_bus.subscribe(
                RequestRotateRGV,
                lambda e: log.warning("correction %.4f deg SWALLOWED (--no-correct)",
                                      e.angle.Deg)))

        from base_core.quantities.models import Time
        if args.integration_ms is not None:
            spectro.config.exposure_time = Time(args.integration_ms, _P2.MILLI)
        if args.averages is not None:
            spectro.config.average = int(args.averages)
        log.warning("spectrometer: %.0f ms x %d averages",
                    spectro.config.exposure_time.value(_P2.MILLI),
                    spectro.config.average)

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

        log.warning("watched %.0f s: %d spectra published, final loop state %s",
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
