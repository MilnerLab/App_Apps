"""
Main-process handle to the ESP301 worker (mirrors RotatorHandle).

Bridges main-bus events to ESP301 IPC requests and exposes typed wrapper methods that
control-loop routines / VMs call directly. Hosted by the ControlReadoutService (the
ESP301 worker lives in the control_readout subprocess alongside the rotator).
"""
from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from control_readout.esp_301.messages import (
    HomeAxis,
    MoveRelative,
    MoveTo,
    SetVelocity,
    StopAxis,
)

from app_apps.io.control_readout.esp_events import RequestMove

class EspHandle(BaseWorkerHandle):
    WORKER_ID = "esp301"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus)

    def _on_attached(self) -> None:
        self._subscribe(RequestMove, self._on_request_move)

    # --- typed API (called by control-loop routines / VMs) -------------

    def move_to(self, axis: int, position: float) -> None:
        self._request(MoveTo(axis=axis, position=position), self._on_reply)

    def move_relative(self, axis: int, delta: float) -> None:
        self._request(MoveRelative(axis=axis, delta=delta), self._on_reply)

    def home(self, axis: int) -> None:
        self._request(HomeAxis(axis=axis), self._on_reply)

    def stop(self, axis: int) -> None:
        self._request(StopAxis(axis=axis), self._on_reply)

    def set_velocity(self, axis: int, velocity: float) -> None:
        self._request(SetVelocity(axis=axis, velocity=velocity), self._on_reply)

    # --- bus-driven path ----------------------------------------------

    def _on_request_move(self, event: RequestMove) -> None:
        self._request(MoveTo(axis=event.axis, position=event.position), self._on_reply)

    def _on_reply(self, reply: OKReply) -> None:
        pass
