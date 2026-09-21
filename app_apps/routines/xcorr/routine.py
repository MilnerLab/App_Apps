"""``XcorrRoutine`` — the cross-correlation grid scan.

The whole run is one method dispatched onto ``BaseRoutine``'s serial ``TaskRunner``,
wrapped in ``run_lifecycle`` — which is what delivers the flush-and-park guarantee (R3).
The sequence is ordinary Python: a loop over setpoints containing a loop over probe
points. It is written that way because that is what it *is*; a nested grid does not map
onto a flat list of phases, and pretending otherwise was what forced the grid to be
flattened at plan time in the first place.

Three things this routine no longer owns, and where they went:

* **Run control** — pause, resume, abort, step — is ``RunControl`` on ``BaseRoutine``.
  This routine only chooses *where* the checkpoints are: between probe points, and
  between setpoints.
* **Moving a stage** is :class:`TranslationStage`, which validates against the stage's
  own soft limits (from the Devices repo) and blocks until motion is genuinely complete.
* **Walking an axis through a list of positions** is :class:`TranslationStageScan`.

One framework hole is worked *with* rather than around:

* **A moving stage cannot be aborted** (G15). ``Device._lock`` *is* ``controller._lock``,
  and ``move_to`` holds it across a blocking ``wait_for_motion``, so an abort from another
  thread waits for the move it is cancelling. This is **accepted, not fixed**: abort takes
  effect at the next probe point. Do not engineer around it without revisiting that
  decision.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from base_core.framework.events.event_bus import EventBus
from base_core.framework.routines.routine_base import BaseRoutine, routine_thread
from base_core.framework.routines.run_control import ScanAborted
from base_core.framework.serialization.h5_utils import now_utc_iso
from base_core.ipc.blocking import wait_for_status
from base_core.ipc.connection_mode import ConnectionMode
from base_core.ipc.worker_handle import BaseWorkerHandle, WorkerStatus

from app_apps.routines.axes import AXIS_ROLES
from app_apps.routines.scanning.translation_stage import TranslationStage
from app_apps.routines.scanning.translation_stage_scan import TranslationStageScan
from app_apps.routines.xcorr.config import XcorrConfig
from app_apps.routines.xcorr.events import (
    XcorrFailed,
    XcorrFinished,
    XcorrGroupWritten,
    XcorrProgress,
    XcorrScanStarted,
    XcorrSteppingHold,
)
from app_apps.routines.xcorr.planner import PlanError, ScanPlan, Setpoint, plan_scan
from app_apps.routines.xcorr.point_collector import XcorrPointCollector
from app_apps.routines.xcorr.spectrum_recorder import XcorrSpectrumRecorder, integration_span_ns
from app_apps.routines.xcorr.storage import XcorrH5Writer, default_run_path

if TYPE_CHECKING:
    from app_apps.io.control_readout.fms300pp.handler import Fms300ppHandle
    from app_apps.io.control_readout.mfa_cc.handler import MfaccHandle
    from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle
    from app_apps.io.oscilloscope.oscilloscope_worker_handler import OscilloscopeWorkerHandle
    from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle

log = logging.getLogger(__name__)

#: How long to wait for a handle to reach RUNNING after start() (A11).
_START_TIMEOUT_S = 20.0
#: Poll interval while waiting for that transition.
_START_POLL_S = 0.05


class XcorrError(RuntimeError):
    """A device command failed, timed out, or the run could not be planned."""


class XcorrRoutine(BaseRoutine):
    """Walk the (grating, delay) grid, sweep the probe at each, write one HDF5 group.

    Which handle plays which role *is* this constructor signature — the parameter names
    are the roles. What each of those stages can do (its model, units and soft limits)
    comes from the stage's own package in the Devices repo, reached through the handle's
    ``SPEC``; what it is called and how far it jogs comes from ``routines/axes.py``.
    Nothing about the hardware is restated here.
    """

    def __init__(
        self,
        bus: EventBus,
        config: XcorrConfig,
        probe: "Fms300ppHandle",
        delay: "MfaccHandle",
        grating: "Uts150ccHandle",
        scope: "OscilloscopeWorkerHandle",
        spectrometer: "SpectrometerWorkerHandle | None" = None,
    ) -> None:
        self._cfg = config
        # One TranslationStage per role. ``before_move`` is the same on all three
        # deliberately: it is the single choke point through which every commanded move
        # passes, so nothing can start moving without the spectrum gate shutting first.
        self._probe = TranslationStage(
            probe, role="probe", timeout_s=config.timeout_s,
            settle_s=config.settle_s, before_move=self._gate_close)
        self._delay = TranslationStage(
            delay, role="delay", timeout_s=config.timeout_s,
            settle_s=config.settle_s, before_move=self._gate_close)
        self._grating = TranslationStage(
            grating, role="grating", timeout_s=config.timeout_s,
            settle_s=config.settle_s, before_move=self._gate_close)
        self._scope = scope
        #: Optional and purely additive. When present, the free-running spectrum stream
        #: is recorded into ``/spectra`` alongside the scan; when absent — or when it
        #: cannot be started — the scan runs exactly as it did before (the operator
        #: loses spectra, never a scan). Optional also keeps every existing construction
        #: site, including the headless runner, working unchanged.
        self._spectrometer = spectrometer
        self._recorder: XcorrSpectrumRecorder | None = None
        #: Reduces the scope's free-running trace stream into measurement points. Unlike
        #: the recorder this one is not optional: it *is* the acquisition path. Built when
        #: the scope reaches RUNNING, because it registers against the handle's buffer.
        self._collector: XcorrPointCollector | None = None
        #: What to ask the scope worker for. What it actually connected to is read back
        #: off the handle after start, and only that goes into the run's provenance.
        self._scope_mode = config.scope_mode
        self._run_path: Path | None = None
        super().__init__(bus)

    # -- public API -------------------------------------------------------
    #
    # is_running / is_paused / is_step_mode and pause / resume / abort / step /
    # set_step_mode are all inherited from BaseRoutine. Note what they now mean here:
    # a pause or an abort lands at the **next probe point** (G15), because an in-flight
    # move cannot be interrupted and neither can an in-flight acquisition. The current
    # combination is flushed with ``status="aborted"`` before the file closes, so no
    # completed data is lost.

    @property
    def run_path(self) -> Path | None:
        """The file the current or most recent run is writing to."""
        return self._run_path

    def start_scan(self) -> None:
        """Begin a run. Returns immediately; the scan executes on the routine thread."""
        if self.is_running:
            log.warning("XcorrRoutine.start_scan() ignored — a run is already in progress")
            return
        # Arm on the caller's thread, before dispatching, so a second start_scan() cannot
        # slip through the guard above while the first is still queued.
        self._control.begin()
        self._run_scan()

    def _hold_for_alignment(
        self, si: int, sp: Setpoint, probe_scan: TranslationStageScan, n_setpoints: int
    ) -> None:
        """Park the probe mid-sweep and wait for the operator (step mode only).

        The probe is driven to the **centre of this setpoint's commanded sweep** before
        the hold. That is where the correlation peak sits — the coarse trial put it at
        50-53% of the window at both grating extremes — so it is the position at which
        maximising the signal is meaningful. Parking anywhere else (in particular the
        sweep's start, where the stage happens to be) would have the operator optimising
        on the baseline tail, which is exactly the mistake this gate exists to prevent.

        The sweep then starts from its true beginning; the mid-point park costs one
        extra probe move per setpoint and nothing else.

        The gate is shut for the duration of the hold so an operator who spends ten
        minutes on a mirror does not bury the run under spectra attributed to one
        stationary point.
        """
        if not self.is_step_mode:
            return
        probe_cmd = probe_scan.centre
        log.info(
            "XCORR setpoint %d/%d: parking probe at %.4f mm (sweep centre) for alignment",
            si + 1, n_setpoints, probe_cmd,
        )
        self._probe.move_to(probe_cmd)
        # Published only once the stages have settled, so a subscriber that starts
        # streaming here is never looking at an optic in flight.
        self._bus.publish(XcorrSteppingHold(
            holding=True,
            setpoint_index=si,
            n_setpoints=n_setpoints,
            grating_mm=sp.grating_mm,
            delay_mm=sp.delay_mm,
            probe_mm=probe_cmd,
        ))
        log.info(
            "XCORR holding at setpoint %d/%d (grating %.4f mm) — waiting for step()",
            si + 1, n_setpoints, sp.grating_mm,
        )
        try:
            self._control.wait_for_step(on_hold=self._gate_close)
        finally:
            # In a finally so an abort raised at the gate still tells the display to
            # stop; a viewer left believing it is still holding keeps polling forever.
            self._bus.publish(XcorrSteppingHold(
                holding=False, setpoint_index=si, n_setpoints=n_setpoints))

    # -- the run ----------------------------------------------------------

    @routine_thread
    def _run_scan(self) -> None:
        writer: XcorrH5Writer | None = None
        try:
            plan = plan_scan(self._cfg)
        except PlanError as exc:
            # Refused before anything moved — which is the entire point of R2/S1.
            log.error("XCORR plan rejected: %s", exc)
            self._control.end()
            self._bus.publish(XcorrFailed(error=str(exc)))
            return
        except Exception as exc:
            # planning must never crash *silently*: an unhandled error here would
            # escape _run_scan without ever publishing XcorrFailed, so the headless
            # harness would wait forever and the still-running control_readout
            # subprocess would be orphaned holding COM7 (defect G25). Publish and
            # clear like any other failure so the run always ends cleanly.
            log.exception("XCORR planning failed")
            self._control.end()
            self._bus.publish(XcorrFailed(error=str(exc)))
            return

        # run_lifecycle owns the rest: XcorrFailed on any raise, the collector
        # unregistered whatever happens, the run marked stopped. Unregistering first is
        # not optional — while we are a registered consumer the coordinator holds every
        # frame waiting for our ack, so a run that ended (cleanly, aborted or failed)
        # must stop consuming or it stalls the stream for the alignment view too.
        with self.run_lifecycle(
            lambda exc: XcorrFailed(
                error=str(exc),
                path=str(self._run_path or ""),
                n_groups_written=writer.n_groups_written if writer else 0,
            ),
            cleanup=self._stop_collector,
        ):
            for w in plan.warnings:
                log.warning("XCORR plan warning: %s", w)

            fine, coarse = plan.probe_step_range_mm
            log.info(
                "XCORR plan: %d setpoint(s), %d probe point(s) total, step %.3f..%.3f mm; "
                "outer axis = %s (%s)",
                len(plan.setpoints), plan.n_points, fine, coarse,
                plan.outer_axis, plan.outer_reason,
            )

            # Announce the run's shape before anything moves, so a live display can set
            # up its position header / progress bar during the handle-start delay.
            gvals = [sp.grating_mm for sp in plan.setpoints]
            dvals = [sp.delay_base_mm for sp in plan.setpoints]
            self._bus.publish(XcorrScanStarted(
                grating_range_mm=(min(gvals), max(gvals)),
                delay_base_range_mm=(min(dvals), max(dvals)),
                probe_base_range_mm=(min(self._cfg.probe_start_mm, self._cfg.probe_stop_mm),
                                     max(self._cfg.probe_start_mm, self._cfg.probe_stop_mm)),
                n_points=plan.n_points,
                n_setpoints=len(plan.setpoints),
            ))

            self._start_handles()

            self._run_path = default_run_path(self._cfg.out_dir, run_name=self._cfg.run_name)
            with XcorrH5Writer(self._run_path) as writer:
                writer.write_config(self._cfg, plan)
                writer.write_provenance("esp301", self._esp301_provenance())
                writer.write_provenance("scope", self._scope_provenance())
                self._start_recorder(writer)
                try:
                    self._scan(plan, writer)
                finally:
                    # Before mark_finished, so the drop count and the last spectra are
                    # in the file by the time the run is stamped complete.
                    self._stop_recorder()
                writer.mark_finished(aborted=self._control.is_aborting)

            # Stages are left wherever they stopped — parked, not homed. R3 asks for
            # stationary, and every move in this loop is blocking, so by the time we are
            # here nothing is moving.
            self._bus.publish(XcorrFinished(
                path=str(self._run_path),
                aborted=self._control.is_aborting,
                n_groups_written=writer.n_groups_written,
                warnings=plan.warnings,
            ))

    def _scan(self, plan: ScanPlan, writer: XcorrH5Writer) -> None:
        """The grid walk. Every exit path leaves the current group flushed."""
        n_points = plan.n_points
        points_done = 0
        n_setpoints = len(plan.setpoints)
        for si, sp in enumerate(plan.setpoints):
            utc_start = now_utc_iso()
            probe_scan = self._probe_scan(si, sp)
            try:
                # Between setpoints is the coarser of this routine's two checkpoints.
                # Nothing has been written for this setpoint yet, so stopping here simply
                # ends the run — there is no partial group to flush.
                self._checkpoint()

                log.info(
                    "XCORR setpoint %d/%d: grating=%.4f mm, delay=%.4f mm (base %.4f + "
                    "correction %.4f); %d probe pts @ %.3f mm (f_max=%.1f GHz)",
                    si + 1, n_setpoints, sp.grating_mm, sp.delay_mm,
                    sp.delay_base_mm, sp.delay_correction_mm,
                    len(sp.probe_base_mm), sp.probe_step_mm, sp.max_freq_ghz,
                )

                # Grating first, then delay: the delay position is a function of the
                # grating position, so this is the order that makes the pair consistent.
                self._grating.move_to(sp.grating_mm)
                self._delay.move_to(sp.delay_mm)

                # Blocks here in step mode until the operator releases this setpoint. A
                # no-op otherwise, so the free-running scan is unchanged.
                self._hold_for_alignment(si, sp, probe_scan, n_setpoints)
            except ScanAborted:
                log.info("XCORR aborted before sweeping setpoint %d/%d", si + 1, n_setpoints)
                break

            rows, aborted = self._sweep_probe(plan, si, sp, probe_scan, points_done, n_points)
            points_done += len(rows)

            writer.write_group(
                sp,
                rows,
                n_traces_per_point=self._cfg.n_traces,
                utc_start=utc_start,
                status="aborted" if aborted else "ok",
            )
            self._bus.publish(XcorrGroupWritten(
                group_name=sp.group_name,
                setpoint_index=si,
                n_setpoints=len(plan.setpoints),
                n_rows=len(rows),
            ))
            if aborted:
                break

    def _sweep_probe(
        self,
        plan: ScanPlan,
        si: int,
        sp: Setpoint,
        probe_scan: TranslationStageScan,
        points_done: int,
        n_points: int,
    ) -> tuple[list[tuple[float, float, float, int]], bool]:
        """Sweep the probe axis at one (grating, delay) combination.

        ``points_done`` is the run-wide count completed before this setpoint, so the
        published progress is monotonic across setpoints even when their sweeps differ
        in length. Returns the rows collected and whether an abort cut it short.

        The abort is caught here rather than allowed to propagate because the rows
        already collected are worth keeping: the caller writes them out as a group with
        ``status="aborted"``, so a run stopped halfway through a sweep still leaves
        every completed point on disk.
        """
        rows: list[tuple[float, float, float, int]] = []
        try:
            for pt in probe_scan:
                mean, std, n = self._acquire_point(pt.commanded)
                rows.append((pt.commanded, mean, std, n))

                self._bus.publish(XcorrProgress(
                    setpoint_index=si,
                    n_setpoints=len(plan.setpoints),
                    probe_index=pt.index,
                    n_probe=pt.n,
                    points_done=points_done + pt.index + 1,
                    n_points=n_points,
                    grating_mm=sp.grating_mm,
                    delay_mm=sp.delay_mm,
                    delay_base_mm=sp.delay_base_mm,
                    probe_mm=pt.commanded,
                    probe_base_mm=pt.base,
                    v_mean_pos=mean,
                ))
        except ScanAborted:
            log.info(
                "XCORR aborted at probe point %d/%d of setpoint %d",
                probe_scan.index + 1, probe_scan.n, si + 1,
            )
            return rows, True
        return rows, False

    def _probe_scan(self, si: int, sp: Setpoint) -> TranslationStageScan:
        """The probe sweep for one setpoint.

        The probe overlap tracks the grating: the base sweep is the delay axis, but the
        stage is commanded to ``base + grating + intercept``. The planner has already
        validated every such position against the soft limits, which is what lets this
        walk be a plain traversal of a fixed list.

        ``before_move`` is this routine's checkpoint, so the operator's pause and abort
        take effect between probe points and never mid-move. ``on_settled`` fires with
        all three stages stationary — and they stay that way until the next iteration —
        so that is the window in which a spectrum can honestly be attributed to a
        position. It spans the scope acquisition, which is the bulk of the dwell.
        """
        return TranslationStageScan(
            self._probe,
            sp.probe_base_mm,
            offset=sp.probe_offset_mm,
            before_move=self._checkpoint,
            on_settled=lambda pt: self._gate_open(sp, si, pt.index, pt.commanded),
        )

    def _checkpoint(self) -> None:
        """This routine's checkpoint: the base one, plus shutting the spectrum gate.

        The stages *are* stationary while paused, so spectra recorded then would be
        honestly labelled — but an operator who pauses for ten minutes would bury one
        probe point under thousands of rows. The gate reopens at the next probe point
        like any other.
        """
        self.checkpoint(on_hold=self._gate_close)

    # -- acquisition ------------------------------------------------------

    def _acquire_point(self, probe_mm: float) -> tuple[float, float, int]:
        """One probe point: ``(v_mean_pos, v_std, n_positive_mean_traces)``.

        The scope free-runs into shared memory, so a point is a burst taken out of that
        stream rather than a request to the instrument: the collector reduces each trace
        to a positive-mean (D3 step 1) and here we do D3 step 2 — the average and spread
        *across* the traces — and count how many of them actually had positive signal.

        ``gate_ns`` is sampled here, after :meth:`_move` returned, so only traces whose
        capture *began* with the stages already stationary are admitted.
        ``in_flight_discard`` drops that many admitted traces on top, as a margin.

        Runs on the routine's own ``TaskRunner`` thread; frames arrive on the IPC reader
        thread, so the wait does not deadlock (same contract as ``_move``).
        """
        collector = self._collector
        if collector is None:
            raise XcorrError(f"acquire at probe {probe_mm:.4f} mm: no trace collector")

        gate_ns = time.time_ns()
        values, counts = collector.collect(
            n_traces=self._cfg.n_traces,
            channel=self._cfg.channel,
            gate_ns=gate_ns,
            skip=self._cfg.in_flight_discard,
            timeout_s=self._cfg.timeout_s,
        )

        if len(values) < self._cfg.n_traces:
            raise XcorrError(
                f"acquire at probe {probe_mm:.4f} mm: only {len(values)} of "
                f"{self._cfg.n_traces} traces within {self._cfg.timeout_s:.0f}s -- check "
                f"that the scope is triggering"
            )
        arr = np.asarray(values, dtype=np.float64)
        # n reported per point is the number of traces that carried positive signal —
        # more informative than a constant n_traces, and 0 flags a dead point.
        n_positive = int(sum(1 for c in counts if c > 0))
        return float(arr.mean()), float(arr.std()), n_positive

    # -- spectrometer recording (additive, never fatal) -------------------

    def _start_recorder(self, writer: XcorrH5Writer) -> None:
        """Begin recording the spectrum stream, or log why we are not.

        Every failure path here is a warning and a ``return``. Unlike the stages and the
        scope, the spectrometer is not something the scan *needs*: an operator who ran a
        four-hour grid should not lose it because a USB spectrometer did not enumerate.
        """
        if self._spectrometer is None:
            return
        try:
            if not self._ensure_spectrometer_running():
                return
            cfg = self._spectrometer.config
            self._recorder = XcorrSpectrumRecorder(
                self._bus,
                self._spectrometer,
                writer,
                span_ns=integration_span_ns(cfg),
                provenance=self._spectrometer_provenance(cfg),
            )
            self._recorder.start()
        except Exception:
            log.exception("XCORR: spectrum recording could not start — scanning without it")
            self._recorder = None

    def _ensure_spectrometer_running(self) -> bool:
        """Start the spectrometer worker if it is idle. Returns whether it is running.

        Deliberately *not* folded into ``_start_handles``: everything in that tuple is
        a precondition of the scan and its absence raises. This one is opportunistic —
        if a device panel already has the spectrometer streaming (the common case, since
        Phase Control is usually open) we simply join the stream.
        """
        handle = self._spectrometer
        assert handle is not None
        if handle.state == WorkerStatus.RUNNING:
            return True
        log.info("XCORR starting spectrometer worker for spectrum recording")
        handle.start()
        if self._wait_for_running(handle):
            return True
        log.warning(
            "XCORR: spectrometer worker did not reach RUNNING within %.0fs — scanning "
            "without spectrum recording. The spm_002 subprocess needs the 32-bit "
            "interpreter and PhotonSpectr.dll; check its log.",
            _START_TIMEOUT_S,
        )
        return False

    def _stop_recorder(self) -> None:
        recorder, self._recorder = self._recorder, None
        if recorder is None:
            return
        try:
            recorder.close()
        except Exception:
            log.exception("XCORR: spectrum recorder shutdown failed")

    def _gate_close(self) -> None:
        """Stop admitting spectra — the stages are about to move."""
        if self._recorder is not None:
            self._recorder.gate_close()

    def _gate_open(self, sp: Setpoint, si: int, probe_index: int, probe_mm: float) -> None:
        """Stamp the now-stationary positions and admit spectra again."""
        if self._recorder is not None:
            self._recorder.gate_open(si, sp.grating_mm, sp.delay_mm, probe_index, probe_mm)

    @staticmethod
    def _spectrometer_provenance(cfg) -> dict[str, object]:
        """The spectrometer settings in force, recorded as ``/spectra`` attributes."""
        from base_core.quantities.models import Prefix

        return {
            "model": "SPM-002 (PhotonSpectr)",
            "device_index": cfg.device_index,
            "exposure_ms": float(cfg.exposure_time.value(Prefix.MILLI)),
            "average": cfg.average,
            "dark_subtraction": cfg.dark_subtraction,
            "mode": cfg.mode,
            "scan_delay": cfg.scan_delay,
            "timestamp_source": "time.time_ns() at end of integration (spm_002.spectrometer)",
        }

    # -- device plumbing --------------------------------------------------

    def _start_handles(self) -> None:
        """Start exactly the workers this routine needs and wait for RUNNING (A11).

        Nothing starts on its own: ``handle.start()`` is gated on
        ``ctx.status == AppStatus.CONNECTED``, which nothing in the application ever
        assigns, so today workers start only when a human clicks Start in a device
        panel and every headless command comes back ``ErrorReply("... not started")``
        (defect G12).

        Deliberately narrow. Flipping ``ctx.status`` globally would also auto-start
        the spectrometer and Andor hardware on every launch, for every user, as a
        side effect of an XCORR fix.
        """
        # The scope config is not sent from here: the scope handle's own start() applies it
        # and chains StartWorker off the reply, which is the only ordering that actually
        # holds (the two messages are handled on different subprocess threads, so sending
        # them in order does not make them apply in order).
        handles = (
            (self._grating.handle, "grating (UTS150CC)"),
            (self._delay.handle, "delay (MFA-CC)"),
            (self._probe.handle, "probe (FMS300PP)"),
            (self._scope, "scope"),
        )
        for handle, label in handles:
            if handle.state == WorkerStatus.RUNNING:
                continue
            log.info("XCORR starting %s worker", label)
            handle.start(self._scope_mode if handle is self._scope else None)

        for handle, label in handles:
            if not self._wait_for_running(handle):
                raise XcorrError(
                    f"{label} worker did not reach RUNNING within {_START_TIMEOUT_S:.0f}s. "
                    f"For a stage: check the control_readout subprocess log — the ESP301 "
                    f"is on COM2 and its connection failure is non-fatal, so the stage may "
                    f"have registered without a working serial link. For the scope: a "
                    f"wedged USBTMC link hangs VISA enumeration itself (pyvisa "
                    f"list_resources() never returns); nothing in software clears that — "
                    f"power-cycle the scope or replug its USB."
                )
        log.info("XCORR: all three stage workers RUNNING")
        if self._scope.connection_mode == ConnectionMode.MOCK:
            # Said again here, at the point of no return, because the banner raised at
            # start scrolls and this run is about to write a file that claims to be data.
            log.warning("XCORR: the scope is SIMULATED — this run records synthetic "
                        "traces, and its provenance will say so")

        # Only now: registering as a consumer makes the coordinator wait on our ack, so it
        # must not happen before the stream it is acking for exists.
        self._collector = XcorrPointCollector(self._bus, self._scope)
        self._collector.start()

    def _stop_collector(self) -> None:
        collector, self._collector = self._collector, None
        if collector is not None:
            collector.close()

    @staticmethod
    def _wait_for_running(handle: BaseWorkerHandle) -> bool:
        """Poll until the handle reports RUNNING, or the start timeout expires."""
        return wait_for_status(
            handle, WorkerStatus.RUNNING,
            timeout_s=_START_TIMEOUT_S, poll_s=_START_POLL_S,
        )

    def _esp301_provenance(self) -> dict[str, object]:
        """What is known about the controller without adding IPC (R5, partial).

        Live ``ID?``/``VE?``/``SL?``/``SR?`` readback needs new request messages on
        the three stage workers, which Build Step 1 does not have. What is recorded
        here is the *configuration in force* — port, role-to-axis binding and the
        limits the plan was validated against — which is what makes a file
        reinterpretable. Extend when the query messages exist.

        Every per-axis value is read from the axis registry rather than restated, so a
        run file cannot claim limits the run was not actually validated against.
        """
        out: dict[str, object] = {
            "port": "COM2",
            "baud": 921600,
            "limits_source": "read live 2026-07-19; see XCORR_SPEC.md §3.1",
            "acquisition": "live — positive-mean per trace, reduced from the scope's "
                           "shared-memory trace stream",
        }
        for axis, role in AXIS_ROLES.items():
            out[f"axis_{axis.value}"] = role.esp_axis
            out[f"model_{axis.value}"] = role.spec.model
            out[f"limits_{axis.value}_mm"] = list(role.spec.limits)
        return out

    def _scope_provenance(self) -> dict[str, object]:
        """The scope configuration in force (R5, partial).

        Records the resource, channel, fixed record length and mock flag — what makes
        a file's amplitudes reinterpretable. The instrument's own live state (coupling,
        trigger, vertical scale) is read subprocess-side by ``TdsScope.provenance`` but
        not yet surfaced over IPC; extend with a provenance request message when needed.
        """
        # Read off the handle, never off the request. A scope that was asked for and did
        # not answer is silently replaced by the mock, and a run recorded against the
        # requested mode would then file synthetic traces as instrument data.
        mode = self._scope.connection_mode
        mocked = mode == ConnectionMode.MOCK
        return {
            "model": "MOCK TDS 2012C" if mocked else "Tektronix TDS 2012C",
            "resource": self._scope.config.resource,
            "channel": self._cfg.channel,
            "record_length": self._scope.config.n_samples,
            "n_traces_per_point": self._cfg.n_traces,
            "in_flight_discard": self._cfg.in_flight_discard,
            "mock": mocked,
            "connection_mode": mode.value if mode is not None else "not_started",
            "connection_mode_requested": self._scope_mode.value,
            "reduction": "within-trace mean of samples > 0, then mean/std across traces (D3)",
            "sample_interval_s": self._scope.dt_s,
            "freshness_gate": (
                "a trace is admitted only when its capture began after the probe move "
                "returned, plus in_flight_discard further traces dropped"
            ),
        }
