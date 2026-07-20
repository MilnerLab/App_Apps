from __future__ import annotations

from typing import Callable

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from control_readout.esp_301.mfa_cc.messages import (
    GetCurrentPosMFACC,
    HomeMFACC,
    MFACCPosReply,
    MFACCPosUpdate,
    MoveMFACCTo,
)

from app_apps.io.control_readout.mfa_cc.events import (
    MfaccWorkerStateChanged,
    NewMfaccPosition,
    RequestMoveMfacc,
)


class MfaccHandle(BaseWorkerHandle):
    """Main-process handle to the MFA-CC ESP301 linear stage."""

    WORKER_ID = "mfacc"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=MfaccWorkerStateChanged)

    def subscribe(self) -> None:
        self._subscribe(RequestMoveMfacc, self._on_request_move)
        self._subscribe_service(MFACCPosUpdate, self._on_position_update)

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
            MoveMFACCTo(position=position),
            (lambda _reply: on_done()) if on_done is not None else self._on_reply,
            (lambda err: on_error(err.error)) if on_error is not None else None,
        )

    def home(self) -> None:
        self._request(HomeMFACC(), self._on_reply)

    def get_position(self) -> None:
        self._request(GetCurrentPosMFACC(), self._on_position_reply)

    def _on_request_move(self, event: RequestMoveMfacc) -> None:
        self._request(MoveMFACCTo(position=event.position), self._on_reply)

    def _on_reply(self, reply: OKReply) -> None:
        pass

    def _on_position_reply(self, reply: MFACCPosReply) -> None:
        self._bus.publish(NewMfaccPosition(position=reply.position))

    def _on_position_update(self, msg: MFACCPosUpdate) -> None:
        self._bus.publish(NewMfaccPosition(position=msg.position))
