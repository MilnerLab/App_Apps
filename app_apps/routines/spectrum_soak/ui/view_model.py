from __future__ import annotations

import logging
import threading
from typing import Callable, TYPE_CHECKING

import numpy as np

from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_view_model import PanelViewModel

from app_apps.routines.spectrum_soak.recorder import (
    SoakH5Writer,
    SpectrumSoakRecorder,
    default_soak_path,
)
from app_apps.routines.spectrum_soak.settings import SoakSettings

if TYPE_CHECKING:
    from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import (
        StabilizationConfig,
    )
    from app_apps.analysis.phase_control.phase_stabilization_handle import (
        PhaseStabilizationHandle,
    )
    from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle

log = logging.getLogger(__name__)

#: (status text, is_running, is_paused) sink the view binds, rendered on the UI thread.
UpdateSink = Callable[[str, bool, bool], None]

#: (wavelength_nm[px], block[n, px]) sink the heatmap binds, rendered on the UI thread.
DataSink = Callable[[np.ndarray, np.ndarray], None]


class SpectrumSoakViewModel(PanelViewModel):
    """Drives one soak recording from the panel.

    Unlike ``XcorrViewModel`` this owns no motion and publishes no events: the recorder
    is a read-only consumer of a stream that is already running. What it does own is the
    *end* of the run. The recorder stops itself once the requested duration has elapsed,
    so something has to notice and close the file; that is :attr:`_watch`, a plain daemon
    thread parked in ``recorder.wait()``. Polling from a Qt timer would work equally well
    and would put a repaint on the same clock as the file close, which is the sort of
    coupling that makes a stall look like data loss.

    Progress arrives on the recorder's writer thread, never Qt's, so every view update is
    marshalled through the dispatcher.
    """

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        spectrometer: "SpectrometerWorkerHandle",
        phase: "PhaseStabilizationHandle",
        config: "StabilizationConfig",
        settings: SoakSettings,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._spectrometer = spectrometer
        self._phase = phase
        self._config = config
        self._settings = settings
        self._recorder: SpectrumSoakRecorder | None = None
        self._watch: threading.Thread | None = None
        self._update: UpdateSink | None = None
        self._data: DataSink | None = None

    @property
    def settings(self) -> SoakSettings:
        return self._settings

    def bind_update(self, sink: UpdateSink) -> None:
        self._update = sink

    def bind_data(self, sink: DataSink) -> None:
        self._data = sink

    def recording_roi(self) -> tuple[float, float] | None:
        """The band this run would record, or None for the whole detector.

        The ROI is followed only while the loop is RUNNING. An ROI left over in the
        config from an earlier session, with stabilization stopped, describes a region
        nobody is currently holding -- cropping to it would quietly throw away the
        detector either side of a stale number.
        """
        from base_core.ipc.worker_handle import WorkerStatus

        if self._phase.state != WorkerStatus.RUNNING:
            return None
        return self._config.roi

    @property
    def is_running(self) -> bool:
        return self._recorder is not None

    # -- commands ---------------------------------------------------------

    def start(self) -> None:
        from base_core.ipc.worker_handle import WorkerStatus
        from base_core.quantities.enums import Prefix

        if self._recorder is not None:
            self._msg("a soak is already recording", MessageLevel.WARNING)
            return
        if self._spectrometer.state != WorkerStatus.RUNNING:
            # Worth its own message: with the spectrometer stopped the recorder would
            # register, wait, and record an empty file, which looks like a broken loop
            # rather than a stopped device.
            self._render("the spectrometer is not running — start it first", running=False)
            self._msg("spectrum soak: the spectrometer is not running", MessageLevel.ERROR)
            return

        s = self._settings
        path = default_soak_path(s.out_dir, tag=s.tag)
        cfg = self._config
        roi = self.recording_roi()
        exposure_ms = float(self._spectrometer.config.exposure_time.value(Prefix.MILLI))
        n_avg = max(1, int(self._spectrometer.config.average))
        # Everything needed to tell two recordings apart afterwards. The loop state is
        # read, not set: this panel never starts or stops stabilization, so what goes in
        # the file is what was actually true while the frames were being taken.
        writer = SoakH5Writer(path, attrs={
            "requested_duration_s": float(s.duration_s),
            "requested_period_s": float(s.period_s),
            "tag": s.tag,
            "exposure_ms": exposure_ms,
            "averages": n_avg,
            # Two different facts, so two attrs: what the loop was fitting, and what
            # this file actually contains. They agree only when the loop was running.
            "roi_nm": ("" if cfg.roi is None else f"{cfg.roi[0]:.3f}-{cfg.roi[1]:.3f}"),
            "recorded_roi_nm": ("" if roi is None else f"{roi[0]:.3f}-{roi[1]:.3f}"),
            "stabilizing": roi is not None or self._loop_running(),
            "lambda_ref_nm": float(cfg.params.lambda_ref.value(Prefix.NANO)),
            "window_nm": (f"{cfg.wavelength_range.min.value(Prefix.NANO):.3f}-"
                          f"{cfg.wavelength_range.max.value(Prefix.NANO):.3f}"),
        })
        self._recorder = SpectrumSoakRecorder(
            self._bus, self._spectrometer, writer,
            period_s=s.period_s, duration_s=s.duration_s, roi=roi,
            on_progress=self._on_progress, on_data=self._on_data,
        )
        self._recorder.start()
        self._watch = threading.Thread(target=self._await_end, name="soak-watch", daemon=True)
        self._watch.start()
        span = "whole detector" if roi is None else f"ROI {roi[0]:.2f}-{roi[1]:.2f} nm"
        self._render(f"recording {span} → {path}", running=True)
        self._msg(f"spectrum soak started: {s.duration_s:.0f} s, {span} → {path.name}")

    def _loop_running(self) -> bool:
        from base_core.ipc.worker_handle import WorkerStatus
        return self._phase.state == WorkerStatus.RUNNING

    def pause(self) -> None:
        """Hold. The consumer stays registered and keeps acking -- pausing the recording
        must not pause the spectrum stream that the loop is running on."""
        rec = self._recorder
        if rec is None or rec.is_paused:
            return
        rec.pause()
        self._render(f"paused at {rec.elapsed_s:.0f}/{self._settings.duration_s:.0f} s — "
                     f"{rec.n_kept} spectra so far", running=True, paused=True)

    def resume(self) -> None:
        rec = self._recorder
        if rec is None or not rec.is_paused:
            return
        rec.resume()
        self._render("resuming", running=True, paused=False)

    def stop(self) -> None:
        """End the recording early. What was recorded is kept — the file is closed, not
        discarded, so an operator who has seen enough does not have to wait out the clock."""
        rec, self._recorder = self._recorder, None
        self._watch = None
        if rec is None:
            return
        rec.close()
        self._render(f"stopped early: {rec.n_kept} spectra → {rec.path}",
                     running=False)
        self._msg(f"spectrum soak stopped: {rec.n_kept} spectra recorded")

    def on_close(self) -> None:
        # Closing the panel must not orphan a registered consumer: the coordinator would
        # keep waiting on its ack and stall the phase loop.
        self.stop()
        super().on_close()

    # -- recorder threads → UI thread -------------------------------------

    def _on_progress(self, n_kept: int, n_seen: int, elapsed_s: float) -> None:
        total = self._settings.duration_s
        self._render(f"recording {elapsed_s:.0f}/{total:.0f} s — "
                     f"{n_kept} spectra kept of {n_seen} seen", running=True)

    def _on_data(self, wl, block) -> None:
        sink = self._data
        if sink is not None:
            self._post(lambda: sink(wl, block))

    def _await_end(self) -> None:
        rec = self._recorder
        if rec is None:
            return
        rec.wait()
        if self._recorder is not rec:
            return  # stopped by hand while we were waiting; stop() owns the close
        self._recorder = None
        rec.close()
        level = MessageLevel.WARNING if rec.n_dropped else MessageLevel.INFO
        dropped = f", {rec.n_dropped} DROPPED" if rec.n_dropped else ""
        self._render(f"FINISHED: {rec.n_kept} spectra{dropped} → {rec.path}",
                     running=False)
        self._msg(f"spectrum soak finished: {rec.n_kept} spectra{dropped}", level)

    # -- plumbing ---------------------------------------------------------

    def _render(self, text: str, *, running: bool, paused: bool = False) -> None:
        sink = self._update
        if sink is not None:
            self._post(lambda: sink(text, running, paused))
