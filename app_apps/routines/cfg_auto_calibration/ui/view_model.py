"""ViewModel for the CFG auto-calibration panel.

Arm-agnostic: the view drives everything through (Arm, ...) calls and the VM routes each to
the right stage handle. It never touches the serial link directly — every move is an IPC
request to the device subprocess (which owns the COM7 lock), so calling these from the Qt
thread is safe (the blocking motion happens in the subprocess, per N1).

Safety rules enforced here:
  * a commanded position is validated against the arm's soft limits before dispatch;
  * moves are gated per-arm on a busy flag cleared by the next position push, so rapid
    jog clicks can't flood the serialized controller;
  * moves/home only dispatch when the arm's worker is RUNNING.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread

from app_apps.io.control_readout.fms300pp.events import (
    Fms300ppWorkerStateChanged,
    NewFms300ppPosition,
)
from app_apps.io.control_readout.fms300pp.handler import Fms300ppHandle
from app_apps.io.control_readout.mfa_cc.events import (
    MfaccWorkerStateChanged,
    NewMfaccPosition,
)
from app_apps.io.control_readout.mfa_cc.handler import MfaccHandle
from app_apps.io.control_readout.uts150cc.events import (
    NewUts150ccPosition,
    Uts150ccWorkerStateChanged,
)
from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle
from app_apps.routines.cfg_auto_calibration.arms import ARM_SPECS, Arm
from app_apps.routines.cfg_auto_calibration.fit import CentrifugeFitMap, FitMapError


class CfgAutoCalibrationViewModel(PanelViewModel):
    # (Arm, position_mm)
    position_changed = Signal(object, float)
    # (Arm, WorkerStatus)
    state_changed = Signal(object, object)
    # (grating_mm, delay_mm) — the send-to solution, for display
    solution_ready = Signal(float, float)
    # fit refreshed: (n_points, rms_f0_hz, rms_df_hz)
    fit_updated = Signal(int, float, float)

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        grating: Uts150ccHandle,
        delay: MfaccHandle,
        probe: Fms300ppHandle,
        fit_map: CentrifugeFitMap,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._fit = fit_map
        self._handles = {Arm.GRATING: grating, Arm.DELAY: delay, Arm.PROBE: probe}
        self._busy = {arm: False for arm in Arm}
        # Last position pushed for each arm (mm); seeds the jog reference and displays.
        self._displayed: dict[Arm, float] = {}

        self._sub(Uts150ccWorkerStateChanged, lambda _: self._on_state(Arm.GRATING))
        self._sub(MfaccWorkerStateChanged, lambda _: self._on_state(Arm.DELAY))
        self._sub(Fms300ppWorkerStateChanged, lambda _: self._on_state(Arm.PROBE))
        self._sub(NewUts150ccPosition, lambda e: self._on_position(Arm.GRATING, e.position))
        self._sub(NewMfaccPosition, lambda e: self._on_position(Arm.DELAY, e.position))
        self._sub(NewFms300ppPosition, lambda e: self._on_position(Arm.PROBE, e.position))

    # -- introspection -------------------------------------------------------
    def worker_status(self, arm: Arm) -> WorkerStatus:
        return self._handles[arm].state

    @property
    def fit_map(self) -> CentrifugeFitMap:
        return self._fit

    def request_positions(self) -> None:
        """Ask each running stage for its current position to seed the displays."""
        for arm, handle in self._handles.items():
            if handle.state == WorkerStatus.RUNNING:
                handle.get_position()

    # -- worker lifecycle (per arm) ------------------------------------------
    def start(self, arm: Arm) -> None:
        self._handles[arm].start()

    def pause(self, arm: Arm) -> None:
        self._handles[arm].pause()

    def resume(self, arm: Arm) -> None:
        self._handles[arm].resume()

    def stop(self, arm: Arm) -> None:
        self._handles[arm].stop()

    # -- manual motion -------------------------------------------------------
    def move_absolute(self, arm: Arm, position_mm: float) -> None:
        spec = ARM_SPECS[arm]
        if not spec.in_limits(position_mm):
            self._msg(
                f"{spec.label}: {position_mm:.4f} mm is outside "
                f"{spec.limit_min_mm}..{spec.limit_max_mm} mm — move refused.",
                MessageLevel.WARNING,
            )
            return
        self._dispatch_move(arm, position_mm)

    def jog(self, arm: Arm, delta_mm: float) -> None:
        current = self._displayed.get(arm)
        if current is None:
            self._msg(
                f"{ARM_SPECS[arm].label}: position unknown yet — cannot jog "
                "(start the stage / read its position first).",
                MessageLevel.WARNING,
            )
            return
        target = current + delta_mm
        spec = ARM_SPECS[arm]
        if not spec.in_limits(target):
            self._msg(
                f"{spec.label}: jog to {target:.4f} mm would leave "
                f"{spec.limit_min_mm}..{spec.limit_max_mm} mm — clamped.",
                MessageLevel.WARNING,
            )
            target = spec.clamp(target)
            if target == current:
                return
        self._dispatch_move(arm, target)

    def home(self, arm: Arm) -> None:
        handle = self._handles[arm]
        if handle.state != WorkerStatus.RUNNING:
            self._not_running(arm)
            return
        if self._busy[arm]:
            self._busy_msg(arm)
            return
        self._busy[arm] = True
        handle.home()

    # -- send-to & recompute -------------------------------------------------
    def send_to(self, center_hz: float, bandwidth_hz: float) -> None:
        """Solve the target to arm positions and move grating + delay to realize it."""
        try:
            grating_mm, delay_mm = self._fit.positions_for(center_hz, bandwidth_hz)
        except FitMapError as exc:
            self._msg(f"Send-to: {exc}", MessageLevel.ERROR)
            return

        problems = []
        for arm, mm in ((Arm.GRATING, grating_mm), (Arm.DELAY, delay_mm)):
            spec = ARM_SPECS[arm]
            if not spec.in_limits(mm):
                problems.append(
                    f"{spec.label} {mm:.4f} mm ∉ {spec.limit_min_mm}..{spec.limit_max_mm} mm"
                )
        if problems:
            self._msg("Send-to refused — " + "; ".join(problems), MessageLevel.ERROR)
            return

        self.solution_ready.emit(grating_mm, delay_mm)
        moved = 0
        for arm, mm in ((Arm.GRATING, grating_mm), (Arm.DELAY, delay_mm)):
            if self._dispatch_move(arm, mm):
                moved += 1
        if moved:
            self._msg(
                f"Send-to: grating→{grating_mm:.4f} mm, delay→{delay_mm:.4f} mm.",
                MessageLevel.INFO,
            )

    def recompute(self, path: str) -> None:
        try:
            result = self._fit.recompute_from_xcorr(path)
        except FitMapError as exc:
            self._msg(f"Recompute failed: {exc}", MessageLevel.ERROR)
            return
        self.fit_updated.emit(result.n_points, result.rms_f0_hz, result.rms_df_hz)
        self._msg(
            f"Recomputed calibration from {result.n_points} points "
            f"(RMS: f0 {result.rms_f0_hz:.3e} Hz, Δf {result.rms_df_hz:.3e} Hz).",
            MessageLevel.INFO,
        )

    # -- internals -----------------------------------------------------------
    def _dispatch_move(self, arm: Arm, position_mm: float) -> bool:
        handle = self._handles[arm]
        if handle.state != WorkerStatus.RUNNING:
            self._not_running(arm)
            return False
        if self._busy[arm]:
            self._busy_msg(arm)
            return False
        self._busy[arm] = True
        handle.move_to(position_mm)
        return True

    def _not_running(self, arm: Arm) -> None:
        self._msg(
            f"{ARM_SPECS[arm].label} stage is not running — start it first.",
            MessageLevel.WARNING,
        )

    def _busy_msg(self, arm: Arm) -> None:
        self._msg(
            f"{ARM_SPECS[arm].label} stage is still moving — wait for it to settle.",
            MessageLevel.INFO,
        )

    @ui_thread
    def _on_position(self, arm: Arm, position_mm: float) -> None:
        self._busy[arm] = False
        self._displayed[arm] = position_mm
        self.position_changed.emit(arm, position_mm)

    @ui_thread
    def _on_state(self, arm: Arm) -> None:
        handle = self._handles[arm]
        status = handle.state
        # Seed the position display when a stage comes up, so jog has a reference
        # before the first move (the stage only pushes position after moves/home).
        if status == WorkerStatus.RUNNING and arm not in self._displayed:
            handle.get_position()
        self.state_changed.emit(arm, status)
