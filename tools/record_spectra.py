"""Record the spectrometer for a fixed duration, with the phase loop on or off.

This exists to answer one question with data instead of with the loop's own opinion of
itself: IS the stabilization holding? Record once with the loop off and once with it on,
under otherwise identical conditions, and compare the fringe drift between the two files.

    # baseline: no loop, 5 minutes
    App_Apps\\.venv\\Scripts\\python.exe tools/record_spectra.py --seconds 300

    # the same again with the loop running and correcting
    App_Apps\\.venv\\Scripts\\python.exe tools/record_spectra.py --seconds 300 --stabilize

Nothing is moved unless --stabilize is passed, and --stabilize --no-correct runs the loop
with the plate held still, which isolates "the loop measures a drift" from "the loop
removes it". The recorder is a read-only consumer of the same spectrum stream the panel
and the loop use, so recording does not change what the loop sees.

Output is one HDF5 file per run (see SoakH5Writer for the layout): wavelength_nm once,
then counts[N, n_pixels] and timestamp_ns[N], plus a corrections/ table of every move the
loop commanded. Every spectrum is recorded; read the time axis from timestamp_ns, since
the spectrometer free-runs and its spacing is its own.
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

log = logging.getLogger("record_spectra")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=60.0,
                    help="how long to record, timed from the FIRST spectrum")
    ap.add_argument("--out", type=Path, default=Path("runs"),
                    help="output directory, or a full .h5 path to write exactly")
    ap.add_argument("--tag", default="", help="label folded into the filename")
    ap.add_argument("--stabilize", action="store_true",
                    help="run the phase loop while recording. Off by default, which is "
                         "the baseline arm of the comparison.")
    ap.add_argument("--no-correct", action="store_true",
                    help="with --stabilize: let the loop measure but swallow its "
                         "corrections, so the plate never turns")
    ap.add_argument("--integration-ms", type=float, default=None,
                    help="override the spectrometer integration time (ms)")
    ap.add_argument("--averages", type=int, default=None,
                    help="override the spectrometer hardware average count")
    ap.add_argument("--roi", type=float, nargs=2, metavar=("LO", "HI"), default=None,
                    help="with --stabilize: the operator ROI in nm, as in the panel")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    qapp = QApplication([])  # noqa: F841  -- must exist before QtDispatcher resolves

    from app import build_container, build_context
    from app_apps.analysis.phase_control.module import PhaseControlModule
    from app_apps.analysis.phase_control.phase_stabilization_handle import (
        PhaseStabilizationHandle,
    )
    from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
        StabilizationConfig,
    )
    from app_apps.app.module import AppModule
    from app_apps.io.control_readout.module import ControlReadoutModule
    from app_apps.io.spectrometer.module import SpectrometerModule
    from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle
    from app_apps.routines.spectrum_soak import (
        SoakH5Writer, SpectrumSoakRecorder, default_soak_path,
    )
    from base_core.framework.modules import ModuleManager
    from base_core.ipc.worker_handle import WorkerStatus
    from base_core.quantities.enums import Prefix
    from base_core.quantities.models import Time

    ctx = build_context()
    c = build_container(ctx)
    # PhaseControlModule is bootstrapped either way: it owns the spectrum consumers the
    # panel registers, and booting a different module set for the two arms would mean
    # comparing two different loads on the stream rather than two loop states.
    mm = ModuleManager([AppModule(), SpectrometerModule(),
                        ControlReadoutModule(), PhaseControlModule()])

    unsubs: list = []
    recorder: SpectrumSoakRecorder | None = None
    try:
        log.info("bootstrapping modules (no window)")
        mm.bootstrap(c, ctx)

        spectro = c.get(SpectrometerWorkerHandle)
        if args.integration_ms is not None:
            spectro.config.exposure_time = Time(args.integration_ms, Prefix.MILLI)
        if args.averages is not None:
            spectro.config.average = int(args.averages)
        exposure_ms = float(spectro.config.exposure_time.value(Prefix.MILLI))
        n_avg = max(1, int(spectro.config.average))
        log.warning("spectrometer: %.0f ms x %d averages (~%.2f Hz)",
                    exposure_ms, n_avg, 1000.0 / max(1e-6, exposure_ms * n_avg))

        log.info("starting the spectrometer")
        spectro.start()
        deadline = time.time() + 20.0
        while spectro.state != WorkerStatus.RUNNING and time.time() < deadline:
            time.sleep(0.1)
        if spectro.state != WorkerStatus.RUNNING:
            log.error("the spectrometer never reached RUNNING (state=%s); nothing to record",
                      spectro.state)
            return 2

        cfg = c.get(StabilizationConfig)
        if args.roi is not None:
            cfg.roi_lo, cfg.roi_hi = float(args.roi[0]), float(args.roi[1])
        if args.stabilize:
            if args.no_correct:
                from app_apps.io.control_readout.rgv.events import RequestRotateRGV
                unsubs.append(ctx.event_bus.subscribe(
                    RequestRotateRGV,
                    lambda e: log.warning("correction %.4f deg SWALLOWED (--no-correct)",
                                          e.angle.Deg)))
            log.warning("starting the phase loop%s",
                        " (measuring only)" if args.no_correct else "")
            phase = c.get(PhaseStabilizationHandle)
            phase.start()
            # The subprocess was handed its config at construction, so mutating the shared
            # object is not enough -- it has to be pushed, and only after start(), which
            # rebuilds the tracker from whatever it holds.
            time.sleep(0.5)
            phase.set_config(cfg)
        else:
            log.warning("phase loop NOT started -- this is the baseline arm")

        out = args.out
        path = out if out.suffix == ".h5" else default_soak_path(out, tag=args.tag)
        writer = SoakH5Writer(path, attrs={
            "requested_duration_s": float(args.seconds),
            "stabilize": bool(args.stabilize),
            "correcting": bool(args.stabilize and not args.no_correct),
            "roi_nm": ("" if cfg.roi is None else f"{cfg.roi[0]:.3f}-{cfg.roi[1]:.3f}"),
            "recorded_roi_nm": ("" if not args.stabilize or cfg.roi is None
                                else f"{cfg.roi[0]:.3f}-{cfg.roi[1]:.3f}"),
            "exposure_ms": exposure_ms,
            "averages": n_avg,
            "lambda_ref_nm": float(cfg.params.lambda_ref.value(Prefix.NANO)),
            "window_nm": (f"{cfg.wavelength_range.min.value(Prefix.NANO):.3f}-"
                          f"{cfg.wavelength_range.max.value(Prefix.NANO):.3f}"),
        })
        # Same rule as the panel: the ROI is only recorded when the loop is actually
        # holding it. With --stabilize absent it is a number nobody is enforcing, and
        # cropping to it would throw away the detector either side for no reason.
        rec_roi = cfg.roi if args.stabilize else None
        recorder = SpectrumSoakRecorder(ctx.event_bus, spectro, writer,
                                        duration_s=args.seconds,
                                        roi=rec_roi)
        recorder.start()

        while not recorder.wait(5.0):
            log.info("recording: %.0f/%.0f s, %d spectra kept of %d seen",
                     recorder.elapsed_s, args.seconds, recorder.n_kept, recorder.n_seen)

        log.warning("done: %d spectra over %.0f s -> %s",
                    recorder.n_kept, recorder.elapsed_s, path)
        if recorder.n_dropped:
            log.error("%d spectra were DROPPED -- the writer could not keep up",
                      recorder.n_dropped)
        return 0
    except KeyboardInterrupt:
        # Ctrl-C is a normal way to end a soak. The file is closed in the finally below,
        # so what was recorded up to the interrupt is kept, not lost.
        log.warning("interrupted -- keeping what was recorded")
        return 0
    finally:
        if recorder is not None:
            recorder.close()
        for u in unsubs:
            try:
                u()
            except Exception:
                pass
        log.info("shutting down")
        mm.shutdown(c, ctx)


if __name__ == "__main__":
    sys.exit(main())
