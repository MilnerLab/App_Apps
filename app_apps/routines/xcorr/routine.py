"""``XcorrRoutine`` — the cross-correlation grid scan.

Deliberately **zero** ``Step`` subclasses. With the grid flattened at plan time
there is nothing left for ``Step`` to sequence: ``Prepare -> Scan -> Finalize``
would be three method calls in a state-machine costume, and ``Step`` is a
three-method ABC with no result, no completion signal and no error channel.
``BaseRoutine`` stays, for its serial ``TaskRunner`` and its ``_unsubs`` discipline.
The whole run is one method dispatched onto that thread, wrapped in ``try/finally``
— which is what actually delivers the flush-and-park guarantee (R3) that step
transitions do not (defect G16, XCORR_TASKS.md §7).

Two framework holes are worked *with* rather than around:

* **``BaseRoutine.stop()`` cannot interrupt a running loop** (G16). ``TaskRunner``'s
  ``_STOP`` goes to the *back* of the queue, so ``stop()`` returns after 5 s having
  achieved nothing while a daemon thread keeps driving hardware. Hence
  :attr:`_abort`, a ``threading.Event`` set from the *caller's* thread — never
  dispatched — and checked at every probe point.
* **A moving stage cannot be aborted** (G15). ``Device._lock`` *is*
  ``controller._lock``, and ``move_to`` holds it across a blocking
  ``wait_for_motion``, so an abort from another thread waits for the move it is
  cancelling. This is **accepted, not fixed**: abort takes effect at the next probe
  point. Do not engineer around it without revisiting that decision.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from base_core.framework.events.event_bus import EventBus
from base_core.framework.routines.routine_base import BaseRoutine, routine_thread
from base_core.framework.serialization.h5_utils import now_utc_iso
from base_core.ipc.worker_handle import BaseWorkerHandle, WorkerStatus

from app_apps.routines.xcorr.config import AXIS_LIMITS, XcorrConfig
from app_apps.routines.xcorr.events import (
    XcorrFailed,
    XcorrFinished,
    XcorrGroupWritten,
    XcorrProgress,
)
from app_apps.routines.xcorr.planner import PlanError, ScanPlan, Setpoint, plan_scan
from app_apps.routines.xcorr.storage import XcorrH5Writer, default_run_path

if TYPE_CHECKING:
    from app_apps.io.control_readout.fms300pp.handler import Fms300ppHandle
    from app_apps.io.control_readout.mfa_cc.handler import MfaccHandle
    from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle

log = logging.getLogger(__name__)

#: How long to wait for a handle to reach RUNNING after start() (A11).
_START_TIMEOUT_S = 20.0
#: Poll interval while waiting for that transition.
_START_POLL_S = 0.05


class XcorrError(RuntimeError):
    """A device command failed, timed out, or the run could not be planned."""


class XcorrRoutine(BaseRoutine):
    """Walk the (grating, delay) grid, sweep the probe at each, write one HDF5 group.

    The role binding *is* this constructor signature — the parameter names are the
    roles. There is no ``AxisRole`` enum and no binding config: that would be an
    abstraction layer over three integers that have not changed since the hardware
    was installed, and the axis constants in the three workers are already correct.
    """

    def __init__(
        self,
        bus: EventBus,
        config: XcorrConfig,
        probe: "Fms300ppHandle",
        delay: "MfaccHandle",
        grating: "Uts150ccHandle",
    ) -> None:
        self._cfg = config
        self._probe = probe
        self._delay = delay
        self._grating = grating

        # Set from the caller's thread, read on the routine thread. NOT dispatched —
        # a dispatched abort would queue behind the very loop it is meant to stop.
        self._abort = threading.Event()
        self._running = threading.Event()
        self._run_path: Path | None = None
        super().__init__(bus)

    # -- public API -------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def run_path(self) -> Path | None:
        """The file the current or most recent run is writing to."""
        return self._run_path

    def start_scan(self) -> None:
        """Begin a run. Returns immediately; the scan executes on the routine thread."""
        if self._running.is_set():
            log.warning("XcorrRoutine.start_scan() ignored — a run is already in progress")
            return
        self._abort.clear()
        self._run_scan()

    def abort(self) -> None:
        """Request an orderly stop.

        Takes effect at the **next probe point** (G15): an in-flight move cannot be
        interrupted, and neither can an in-flight acquisition. The current
        combination is flushed with ``status="aborted"`` before the file closes, so
        no completed data is lost.
        """
        if not self._running.is_set():
            return
        log.info("XcorrRoutine: abort requested — will stop at the next probe point")
        self._abort.set()

    # -- the run ----------------------------------------------------------

    @routine_thread
    def _run_scan(self) -> None:
        self._running.set()
        writer: XcorrH5Writer | None = None
        try:
            plan = plan_scan(self._cfg)
        except PlanError as exc:
            # Refused before anything moved — which is the entire point of R2/S1.
            log.error("XCORR plan rejected: %s", exc)
            self._running.clear()
            self._bus.publish(XcorrFailed(error=str(exc)))
            return

        for w in plan.warnings:
            log.warning("XCORR plan warning: %s", w)

        log.info(
            "XCORR plan: %d setpoint(s) x %d probe point(s) = %d points; outer axis "
            "= %s (%s)",
            len(plan.setpoints), len(plan.probe_mm), plan.n_points,
            plan.outer_axis, plan.outer_reason,
        )

        try:
            self._start_handles()

            self._run_path = default_run_path(self._cfg.out_dir)
            with XcorrH5Writer(self._run_path) as writer:
                writer.write_config(self._cfg, plan)
                writer.write_provenance("esp301", self._esp301_provenance())
                self._scan(plan, writer)
                writer.mark_finished(aborted=self._abort.is_set())

            self._bus.publish(XcorrFinished(
                path=str(self._run_path),
                aborted=self._abort.is_set(),
                n_groups_written=writer.n_groups_written,
                warnings=plan.warnings,
            ))
        except Exception as exc:
            log.exception("XCORR run failed")
            self._bus.publish(XcorrFailed(
                error=str(exc),
                path=str(self._run_path or ""),
                n_groups_written=writer.n_groups_written if writer else 0,
            ))
        finally:
            # Stages are left wherever they stopped — parked, not homed. R3 asks for
            # stationary, and every move in this loop is blocking, so by the time we
            # are here nothing is moving.
            self._running.clear()

    def _scan(self, plan: ScanPlan, writer: XcorrH5Writer) -> None:
        """The grid walk. Every exit path leaves the current group flushed."""
        n_points = plan.n_points
        points_done = 0
        for si, sp in enumerate(plan.setpoints):
            if self._abort.is_set():
                log.info("XCORR aborted before setpoint %d/%d", si + 1, len(plan.setpoints))
                break

            utc_start = now_utc_iso()
            log.info(
                "XCORR setpoint %d/%d: grating=%.4f mm, delay=%.4f mm (base %.4f + "
                "correction %.4f); %d probe pts @ %.3f mm (f_max=%.1f GHz)",
                si + 1, len(plan.setpoints), sp.grating_mm, sp.delay_mm,
                sp.delay_base_mm, sp.delay_correction_mm,
                len(sp.probe_base_mm), sp.probe_step_mm, sp.max_freq_ghz,
            )

            # Grating first, then delay: the delay position is a function of the
            # grating position, so this is the order that makes the pair consistent.
            self._move(self._grating, sp.grating_mm, "grating")
            self._move(self._delay, sp.delay_mm, "delay")

            rows, aborted = self._sweep_probe(plan, si, sp, points_done, n_points)
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
        points_done: int,
        n_points: int,
    ) -> tuple[list[tuple[float, float, float, int]], bool]:
        """Sweep the probe axis at one (grating, delay) combination.

        ``points_done`` is the run-wide count completed before this setpoint, so the
        published progress is monotonic across setpoints even when their sweeps differ
        in length. Returns the rows collected and whether an abort cut it short.
        """
        n_probe = len(sp.probe_base_mm)
        rows: list[tuple[float, float, float, int]] = []
        for pi, p_base in enumerate(sp.probe_base_mm):
            if self._abort.is_set():
                log.info(
                    "XCORR aborted at probe point %d/%d of setpoint %d",
                    pi + 1, n_probe, si + 1,
                )
                return rows, True

            # The probe overlap tracks the grating: the base sweep is the delay axis,
            # but the stage is commanded to base + grating + intercept (planner has
            # already validated every such position against the soft limits).
            p_cmd = p_base + sp.probe_offset_mm
            self._move(self._probe, p_cmd, "probe")
            mean, std, n = self._acquire_point(p_cmd)
            rows.append((p_cmd, mean, std, n))

            self._bus.publish(XcorrProgress(
                setpoint_index=si,
                n_setpoints=len(plan.setpoints),
                probe_index=pi,
                n_probe=n_probe,
                points_done=points_done + pi + 1,
                n_points=n_points,
                grating_mm=sp.grating_mm,
                delay_mm=sp.delay_mm,
                probe_mm=p_cmd,
                v_mean_pos=mean,
            ))
        return rows, False

    # -- acquisition ------------------------------------------------------

    def _acquire_point(self, probe_mm: float) -> tuple[float, float, int]:
        """One probe point: ``(v_mean_pos, v_std, n_traces)``.

        **Stubbed** — returns zeros. Build Step 1 proves motion, planning, storage
        and DI on real hardware while the laser is off, when the scope can only
        produce noise anyway. Build Step 2 replaces this body with a blocking
        ``AcquirePoint`` request to the oscilloscope worker, which gates on
        ``ACQuire:NUMACq?`` and reduces subprocess-side so only scalars cross IPC.

        Override in a subclass, or replace here, when B6 lands.
        """
        return 0.0, 0.0, self._cfg.n_traces

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
        handles = (
            (self._grating, "grating (UTS150CC)"),
            (self._delay, "delay (MFA-CC)"),
            (self._probe, "probe (FMS300PP)"),
        )
        for handle, label in handles:
            if handle.state == WorkerStatus.RUNNING:
                continue
            log.info("XCORR starting %s worker", label)
            handle.start()

        for handle, label in handles:
            if not self._wait_for_running(handle):
                raise XcorrError(
                    f"{label} worker did not reach RUNNING within {_START_TIMEOUT_S:.0f}s. "
                    f"Check the control_readout subprocess log — the ESP301 is on COM7 "
                    f"and its connection failure is now non-fatal, so the stage may have "
                    f"registered without a working serial link."
                )
        log.info("XCORR: all three stage workers RUNNING")

    @staticmethod
    def _wait_for_running(handle: BaseWorkerHandle) -> bool:
        """Poll until the handle reports RUNNING, or the start timeout expires.

        Polling rather than event-driven on purpose. ``WorkerState`` does publish a
        no-arg event on every transition, but a ``_start()`` that *raises* in the
        subprocess sends no reply at all — ``BaseWorker._on_start_cmd`` calls
        ``_start()`` before ``_reply_ok`` with no try/except, and the exception is
        swallowed by the worker's ``TaskRunner``. So the failure case produces no
        transition and no error reply, and there would be nothing to wake on. The
        timeout is the only thing that distinguishes it from a slow start.
        """
        clock = threading.Event()
        deadline = time.monotonic() + _START_TIMEOUT_S
        while time.monotonic() < deadline:
            if handle.state == WorkerStatus.RUNNING:
                return True
            clock.wait(_START_POLL_S)
        return handle.state == WorkerStatus.RUNNING

    def _move(self, handle, position: float, role: str) -> None:
        """Blocking, reply-correlated absolute move, plus the configured dwell.

        Blocking on the routine's own ``TaskRunner`` thread is safe — replies arrive
        on the IPC reader thread, so nothing deadlocks. **Do not** call this from an
        EventBus handler, which runs on the publisher's thread.
        """
        lo, hi = AXIS_LIMITS[role]
        if not (lo <= position <= hi):
            # Belt and braces: the planner already refused out-of-range setpoints,
            # so reaching here means a bug, not a bad config.
            raise XcorrError(f"{role} setpoint {position:.4f} mm outside [{lo}, {hi}]")

        self._call(
            lambda ok, err: handle.move_to(position, on_done=ok, on_error=err),
            what=f"{role} move to {position:.4f} mm",
        )
        if self._cfg.settle_s > 0:
            threading.Event().wait(self._cfg.settle_s)

    def _call(
        self,
        submit: Callable[[Callable[[], None], Callable[[str], None]], None],
        *,
        what: str,
    ) -> None:
        """Turn an async ``_request`` into a blocking call, correlated on the reply.

        There is no in-repo precedent for this: every other handle's ``_on_reply`` is
        ``pass``, discarding both the result and the error. The framework already
        supports it — ``BaseWorkerHandle._request(msg, on_reply, on_error)`` takes
        both callbacks — this is simply the first caller to use them.

        The alternative, publishing a completion event, cannot work: the event
        carries no request id and no target position, so it cannot be correlated to
        a specific move, and it races the device panel's own live ``RequestMove*``
        subscription.
        """
        done = threading.Event()
        error: list[str] = []

        def on_ok() -> None:
            done.set()

        def on_err(message: str) -> None:
            error.append(message)
            done.set()

        submit(on_ok, on_err)

        if not done.wait(self._cfg.timeout_s):
            raise XcorrError(f"{what}: no reply within {self._cfg.timeout_s:.0f}s")
        if error:
            raise XcorrError(f"{what}: {error[0]}")

    def _esp301_provenance(self) -> dict[str, object]:
        """What is known about the controller without adding IPC (R5, partial).

        Live ``ID?``/``VE?``/``SL?``/``SR?`` readback needs new request messages on
        the three stage workers, which Build Step 1 does not have. What is recorded
        here is the *configuration in force* — port, role-to-axis binding and the
        limits the plan was validated against — which is what makes a file
        reinterpretable. Extend when the query messages exist.
        """
        return {
            "port": "COM7",
            "baud": 921600,
            "axis_probe": 1,
            "axis_delay": 2,
            "axis_grating": 3,
            "model_probe": "FMS300PP",
            "model_delay": "MFA-CC",
            "model_grating": "UTS150CC",
            "limits_probe_mm": list(AXIS_LIMITS["probe"]),
            "limits_delay_mm": list(AXIS_LIMITS["delay"]),
            "limits_grating_mm": list(AXIS_LIMITS["grating"]),
            "limits_source": "read live 2026-07-19; see XCORR_SPEC.md §3.1",
            "acquisition": "STUBBED — Build Step 1 records zeros; no scope was read",
        }
