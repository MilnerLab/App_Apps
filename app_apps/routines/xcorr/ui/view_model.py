from __future__ import annotations

import logging
from typing import Callable, TYPE_CHECKING

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_view_model import PanelViewModel

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
        self._update: UpdateSink | None = None

        self._sub(XcorrProgress, self._on_progress)
        self._sub(XcorrGroupWritten, self._on_group)
        self._sub(XcorrFinished, self._on_finished)
        self._sub(XcorrFailed, self._on_failed)

    @property
    def settings(self) -> XcorrSettings:
        return self._settings

    def bind_update(self, sink: UpdateSink) -> None:
        """Register the view's (text, running) renderer."""
        self._update = sink

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
