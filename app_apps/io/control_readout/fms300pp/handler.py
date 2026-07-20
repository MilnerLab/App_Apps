from __future__ import annotations

from typing import Callable

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

    def move_to(
        self,
        position: float,
        on_done: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        """Command an absolute move, in mm.

        The worker replies only *after* ``wait_for_motion`` returns, so the reply is
        a genuine motion-complete signal. Pass ``on_done``/``on_error`` when you need
        to sequence the next command behind this one: the callbacks are threaded
        through ``_request``, which correlates them to this specific request id.

        Do not substitute a completion event on the bus — it carries no request id
        and no target position, so it cannot be correlated, and it would race the
        device panel's own live ``RequestMoveFms300pp`` subscription.

        Callbacks run on the IPC reader thread. Keep them short.
        """
        self._request(
            MoveFMS300PPTo(position=position),
            (lambda _reply: on_done()) if on_done is not None else self._on_reply,
            (lambda err: on_error(err.error)) if on_error is not None else None,
        )

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
