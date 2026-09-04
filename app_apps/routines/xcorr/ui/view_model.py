from __future__ import annotations

import logging
from typing import Callable, TYPE_CHECKING

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_view_model import PanelViewModel

from app_apps.io.control_readout.mfa_cc.events import NewMfaccPosition
from app_apps.io.control_readout.uts150cc.events import NewUts150ccPosition
from app_apps.routines.xcorr.events import (
    XcorrFailed,
    XcorrFinished,
    XcorrGroupWritten,
    XcorrProgress,
)
from app_apps.routines.xcorr.routine import XcorrRoutine
from app_apps.routines.xcorr.settings import XcorrSettings

if TYPE_CHECKING:
    from app_apps.io.control_readout.fms300pp.handler import Fms300ppHandle
    from app_apps.io.control_readout.mfa_cc.handler import MfaccHandle
    from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle
    from app_apps.io.oscilloscope.oscilloscope_worker_handler import OscilloscopeWorkerHandle
    from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle

log = logging.getLogger(__name__)

#: (status text, is_running) sink the view binds so it can render on the UI thread.
UpdateSink = Callable[[str, bool], None]

#: Called on the UI thread after the settings were changed behind the form's back, so
#: the widgets can be repopulated from them.
ReloadSink = Callable[[], None]


class XcorrViewModel(PanelViewModel):
    """Drives an XCORR scan from the panel.

    Mirrors ``CfgCalibrationViewModel``: it constructs the routine directly from
    the injected handles rather than resolving the DI singleton, so each Start is
    a *fresh* :class:`XcorrRoutine` (``BaseRoutine`` starts a serial ``TaskRunner``
    in its constructor — a stopped one must never be reused). The frozen
    :class:`XcorrConfig` is built from the mutable :class:`XcorrSettings` the form
    edits; the planner still validates every setpoint, so a bad range surfaces as
    ``XcorrFailed`` here **before anything moves**.

    Routine events arrive on the routine's ``TaskRunner`` thread, never Qt's, so
    every view update is marshalled through the dispatcher (:meth:`_post`).
    """

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        probe: "Fms300ppHandle",
        delay: "MfaccHandle",
        grating: "Uts150ccHandle",
        scope: "OscilloscopeWorkerHandle",
        settings: XcorrSettings,
        spectrometer: "SpectrometerWorkerHandle | None" = None,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._probe = probe
        self._delay = delay
        self._grating = grating
        self._scope = scope
        self._spectrometer = spectrometer
        self._settings = settings
        self._routine: XcorrRoutine | None = None
        # Panel-level state, not routine state: a fresh Start builds a fresh routine, so
        # the operator's choice has to survive here and be re-applied there.
        self._step_mode = True
        self._update: UpdateSink | None = None
        self._reload: ReloadSink | None = None
        # Latest position seen for the two axes a probe-only run must not command.
        # Fed by the stages' own spontaneous position pushes and by the explicit reads
        # `pin_stages_here` issues; None until one arrives.
        self._grating_pos_mm: float | None = None
        self._delay_pos_mm: float | None = None

        self._sub(NewUts150ccPosition, self._on_grating_pos)
        self._sub(NewMfaccPosition, self._on_delay_pos)
        self._sub(XcorrProgress, self._on_progress)
        self._sub(XcorrGroupWritten, self._on_group)
        self._sub(XcorrFinished, self._on_finished)
        self._sub(XcorrFailed, self._on_failed)

    def set_step_mode(self, enabled: bool) -> None:
        """Arm/disarm operator-advanced stepping. Persists across the routine's life
        only — a fresh Start builds a fresh routine, so it is re-applied there."""
        self._step_mode = enabled
        if self._routine is not None and self._routine.is_running:
            self._routine.set_step_mode(enabled)
            self._render(
                "step mode — the run holds at each grating position until Step"
                if enabled else "step mode off — run free-running",
                running=True,
            )

    def step(self) -> None:
        if self._routine is None or not self._routine.is_running:
            return
        self._routine.step()

    @property
    def settings(self) -> XcorrSettings:
        return self._settings

    def bind_update(self, sink: UpdateSink) -> None:
        """Register the view's (text, running) renderer."""
        self._update = sink

    def bind_reload(self, sink: ReloadSink) -> None:
        """Register the view's repopulate-from-settings callback."""
        self._reload = sink

    # -- probe-only ------------------------------------------------------

    def _on_grating_pos(self, e: NewUts150ccPosition) -> None:
        self._grating_pos_mm = e.position

    def _on_delay_pos(self, e: NewMfaccPosition) -> None:
        self._delay_pos_mm = e.position

    def pin_stages_here(self) -> None:
        """Arm a probe-only run: pin the grating and delay ranges to where those two
        stages are standing right now, and stop the routine from commanding them.

        The pin is what keeps the recorded coordinates honest -- probe-only skips the
        moves, so the file's grating/delay attributes are only true if they already
        match the hardware. Positions come from the two stages' own position events;
        a read is requested here as well, but it lands asynchronously, so a first press
        with nothing cached yet asks the operator to press again rather than guessing.
        """
        # Read-only queries. Safe on a stage that must not move: GetCurrentPos never
        # commands motion.
        self._grating.get_position()
        self._delay.get_position()

        g = self._grating_pos_mm
        d = self._delay_pos_mm
        if g is None or d is None:
            missing = ", ".join(
                n for n, v in (("grating", g), ("delay", d)) if v is None
            )
            self._render(
                f"no position yet for {missing} — start that stage's panel, "
                f"then press Pin stages here again",
                running=False,
            )
            self._msg(f"XCORR: no position yet for {missing}", MessageLevel.WARNING)
            return

        st = self._settings
        # The routine commands delay = base + slope*grating + intercept, so the base that
        # reproduces the live position has the correction subtracted back out. Recorded
        # coordinates then match the hardware exactly, correction included.
        base = d - (st.delay_slope * g + st.delay_intercept_mm)
        st.grating_start_mm = g
        st.grating_stop_mm = g
        st.delay_base_start_mm = base
        st.delay_base_stop_mm = base
        st.probe_only = True

        if self._reload is not None:
            self._post(self._reload)
        self._render(
            f"probe only — grating pinned at {g:.4f} mm, delay at {d:.4f} mm "
            f"(base {base:.4f}). Neither stage will be commanded.",
            running=False,
        )
        self._msg("XCORR: probe-only armed; delay and grating pinned")

    # -- commands ---------------------------------------------------------

    def start(self) -> None:
        if self._routine is not None and self._routine.is_running:
            self._msg("XCORR scan already running", MessageLevel.WARNING)
            return
        # A prior, finished routine still holds a TaskRunner thread — retire it.
        if self._routine is not None:
            self._routine.stop()
            self._routine = None

        cfg = self._settings.to_config()
        self._routine = XcorrRoutine(
            bus=self._bus,
            config=cfg,
            probe=self._probe,
            delay=self._delay,
            grating=self._grating,
            scope=self._scope,
            spectrometer=self._spectrometer,
        )
        # Re-applied here rather than carried on the routine: each Start builds a new one.
        self._routine.set_step_mode(self._step_mode)
        self._render(f"starting scan → {cfg.out_dir}", running=True)
        self._msg("XCORR scan started")
        self._routine.start_scan()

    def abort(self) -> None:
        if self._routine is None or not self._routine.is_running:
            return
        self._routine.abort()
        self._render("abort requested — stopping at the next probe point", running=True)

    def pause(self) -> None:
        if self._routine is None or not self._routine.is_running:
            return
        self._routine.pause()
        self._render("pause requested — holding at the next probe point", running=True)

    def resume(self) -> None:
        if self._routine is None or not self._routine.is_running:
            return
        self._routine.resume()
        self._render("resuming — scan continuing", running=True)

    def on_close(self) -> None:
        if self._routine is not None:
            self._routine.stop()
            self._routine = None
        super().on_close()

    # -- event handlers (routine thread → UI thread) ----------------------

    def _on_progress(self, e: XcorrProgress) -> None:
        self._render(
            f"point {e.points_done}/{e.n_points}  g={e.grating_mm:.3f} "
            f"d={e.delay_mm:.3f} probe={e.probe_mm:.3f} mm  →  {e.v_mean_pos:.4g}",
            running=True,
        )

    def _on_group(self, e: XcorrGroupWritten) -> None:
        self._render(
            f"flushed /scans/{e.group_name} ({e.n_rows} rows) — "
            f"{e.setpoint_index + 1}/{e.n_setpoints} combination(s)",
            running=True,
        )

    def _on_finished(self, e: XcorrFinished) -> None:
        tag = " (ABORTED)" if e.aborted else ""
        self._render(f"FINISHED{tag}: {e.n_groups_written} group(s) → {e.path}", running=False)
        self._msg(f"XCORR scan finished{tag}: {e.n_groups_written} group(s)")

    def _on_failed(self, e: XcorrFailed) -> None:
        self._render(f"FAILED after {e.n_groups_written} group(s): {e.error}", running=False)
        self._msg(f"XCORR scan failed: {e.error}", MessageLevel.ERROR)

    # -- plumbing ---------------------------------------------------------

    def _render(self, text: str, *, running: bool) -> None:
        """Marshal a status update onto the Qt thread (handlers run off-thread)."""
        sink = self._update
        if sink is not None:
            self._post(lambda: sink(text, running))
