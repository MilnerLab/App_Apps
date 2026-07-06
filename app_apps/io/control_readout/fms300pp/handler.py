from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from control_readout.esp_301.fms300pp.messages import (
    FMS300PPPosReply,
    FMS300PPPosUpdate,
    GetCurrentPosFMS300PP,
    HomeFMS300PP,
    MoveFMS300PPTo,
)

from app_apps.io.control_readout.fms300pp.events import (
    Fms300ppWorkerStateChanged,
    NewFms300ppPosition,
    RequestMoveFms300pp,
)


class Fms300ppHandle(BaseWorkerHandle):
    """Main-process handle to the FMS300PP ESP301 linear stage."""

    WORKER_ID = "fms300pp"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=Fms300ppWorkerStateChanged)

    def subscribe(self) -> None:
        self._subscribe(RequestMoveFms300pp, self._on_request_move)
        self._subscribe_service(FMS300PPPosUpdate, self._on_position_update)

    def move_to(self, position: float) -> None:
        self._request(MoveFMS300PPTo(position=position), self._on_reply)

    def home(self) -> None:
        self._request(HomeFMS300PP(), self._on_reply)

    def get_position(self) -> None:
        self._request(GetCurrentPosFMS300PP(), self._on_position_reply)

    def _on_request_move(self, event: RequestMoveFms300pp) -> None:
        self._request(MoveFMS300PPTo(position=event.position), self._on_reply)

    def _on_reply(self, reply: OKReply) -> None:
        pass

    def _on_position_reply(self, reply: FMS300PPPosReply) -> None:
        self._bus.publish(NewFms300ppPosition(position=reply.position))

    def _on_position_update(self, msg: FMS300PPPosUpdate) -> None:
        self._bus.publish(NewFms300ppPosition(position=msg.position))
