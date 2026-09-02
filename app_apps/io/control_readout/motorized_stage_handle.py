from __future__ import annotations

from typing import Callable, ClassVar

from base_core.ipc.message import Message, Reply, Request
from base_core.ipc.worker_handle import BaseWorkerHandle


class MotorizedStageHandle(BaseWorkerHandle):
    """Main-process handle for a single-axis motorized stage/rotator.

    Subclasses declare REQUEST_MOVE_EVENT/POS_UPDATE_MSG (their domain event
    and spontaneous IPC push type) and implement the hook methods below to
    bridge their device-specific IPC messages and domain events.
    """

    REQUEST_MOVE_EVENT: ClassVar[type]
    POS_UPDATE_MSG: ClassVar[type[Message]]

    def subscribe(self) -> None:
        self._subscribe(self.REQUEST_MOVE_EVENT, self._on_request_move)
        self._subscribe_service(self.POS_UPDATE_MSG, self._on_position_update)

    def move_to(
        self,
        value,
        on_done: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        """Command an absolute move. Callbacks are optional.

        The workers reply only *after* ``wait_for_motion`` returns, so the reply is a
        genuine motion-complete signal and ``on_done`` is correlated to THIS request by
        its id. That correlation is the whole point: a completion event on the bus
        carries neither a request id nor a target, so it cannot be matched to the move
        that caused it, and it would race the device panel's own live move subscription.

        Callbacks run on the IPC reader thread. Keep them short.
        """
        self._request(
            self._build_move_msg(value),
            (lambda _reply: on_done()) if on_done is not None else self._on_move_reply,
            (lambda err: on_error(err.error)) if on_error is not None else None,
        )

    def home(self) -> None:
        self._request(self._build_home_msg(), self._on_home_reply)

    def get_position(self) -> None:
        self._request(self._build_get_pos_msg(), self._on_position_reply)

    def _on_request_move(self, event) -> None:
        self.move_to(self._move_value_from_event(event))

    def _on_move_reply(self, reply: Reply) -> None:
        """Override to react to a successful move (default: no-op)."""

    def _on_home_reply(self, reply: Reply) -> None:
        """Override to react to a successful home (default: no-op)."""

    def _on_position_update(self, msg: Message) -> None:
        self._bus.publish(self._build_position_event(self._msg_value(msg)))

    def _on_position_reply(self, reply: Reply) -> None:
        self._bus.publish(self._build_position_event(self._msg_value(reply)))

    # --- hooks: subclasses implement against their own message types -----

    def _build_move_msg(self, value) -> Request:
        raise NotImplementedError

    def _build_home_msg(self) -> Request:
        raise NotImplementedError

    def _build_get_pos_msg(self) -> Request:
        raise NotImplementedError

    def _move_value_from_event(self, event):
        raise NotImplementedError

    def _msg_value(self, msg):
        raise NotImplementedError

    def _build_position_event(self, value):
        raise NotImplementedError
