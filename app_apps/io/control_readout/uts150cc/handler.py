from __future__ import annotations

from typing import Callable

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from control_readout.esp_301.uts150cc.messages import (
    GetCurrentPosUTS150CC,
    HomeUTS150CC,
    MoveUTS150CCTo,
    UTS150CCPosReply,
    UTS150CCPosUpdate,
)

from app_apps.io.control_readout.uts150cc.events import (
    NewUts150ccPosition,
    RequestMoveUts150cc,
    Uts150ccWorkerStateChanged,
)


class Uts150ccHandle(BaseWorkerHandle):
    """Main-process handle to the UTS150CC ESP301 linear stage."""

    WORKER_ID = "uts150cc"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=Uts150ccWorkerStateChanged)

    def subscribe(self) -> None:
        self._subscribe(RequestMoveUts150cc, self._on_request_move)
        self._subscribe_service(UTS150CCPosUpdate, self._on_position_update)

    def move_to(
        self,
        position: float,
        on_done: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        """Command an absolute move, in mm. See ``Fms300ppHandle.move_to``.

        The reply arrives after motion completes, so ``on_done`` is a motion-complete
        signal correlated to this request. Callbacks run on the IPC reader thread.
        """
        self._request(
            MoveUTS150CCTo(position=position),
            (lambda _reply: on_done()) if on_done is not None else self._on_reply,
            (lambda err: on_error(err.error)) if on_error is not None else None,
        )

    def home(self) -> None:
        self._request(HomeUTS150CC(), self._on_reply)

    def get_position(self) -> None:
        self._request(GetCurrentPosUTS150CC(), self._on_position_reply)

    def _on_request_move(self, event: RequestMoveUts150cc) -> None:
        self._request(MoveUTS150CCTo(position=event.position), self._on_reply)

    def _on_reply(self, reply: OKReply) -> None:
        pass

    def _on_position_reply(self, reply: UTS150CCPosReply) -> None:
        self._bus.publish(NewUts150ccPosition(position=reply.position))

    def _on_position_update(self, msg: UTS150CCPosUpdate) -> None:
        self._bus.publish(NewUts150ccPosition(position=msg.position))
