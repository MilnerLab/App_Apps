from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from base_core.math.models import Angle
from control_readout.ell14.messages import CurrentELL14Position, HomeELL14Rotator, RotateELL14

from app_apps.io.control_readout.ell14.events import (
    ELL14RotatorHomed,
    ELL14WorkerStateChanged,
    NewELL14Angle,
    RequestRotate,
)


class ELL14RotatorHandle(BaseWorkerHandle):
    """
    Main-process handle to RotatorWorker.

    Bridges RequestRotate events from the main bus to Rotate IPC requests
    sent to the subprocess. The PhaseTrackingHandle publishes RequestRotate;
    this handle receives it and commands the physical rotator.
    """

    WORKER_ID = "rotator"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=ELL14WorkerStateChanged)
        self._current_Angle: Angle = None

    def subscribe(self) -> None:
        self._subscribe(RequestRotate, self._on_request_rotate)
        self._subscribe_service(CurrentELL14Position, self._on_current_position)

    def home(self) -> None:
        self._request(HomeELL14Rotator(), self._on_home_reply)

    def _on_request_rotate(self, event: RequestRotate) -> None:
        self._request(RotateELL14(angle=event.angle), self._on_rotate_reply)

    def _on_rotate_reply(self, reply: OKReply) -> None:
        pass

    def _on_home_reply(self, reply: OKReply) -> None:
        self._bus.publish(ELL14RotatorHomed())

    def _on_current_position(self, msg: CurrentELL14Position) -> None:
        self._current_Angle = msg.angle
        self._bus.publish(NewELL14Angle(self._current_Angle))
