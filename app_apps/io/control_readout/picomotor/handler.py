"""Main-process handle to the mirror picomotors (New Focus 8742, manual tip/tilt)."""
from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from control_readout.picomotor.messages import (
    QuerySteps,
    StepBy,
    StepsMoved,
    StepsReply,
    StepTo,
    ZeroAxis,
)

from app_apps.io.control_readout.picomotor.events import (
    PicomotorStepsChanged,
    PicomotorWorkerStateChanged,
    RequestPicomotorSteps,
    RequestStepPicomotor,
    RequestStepPicomotorTo,
    RequestZeroPicomotor,
)


class PicomotorHandle(BaseWorkerHandle):
    """Main-process handle to the mirror picomotors (manual).

    Keeps the last known step counter per axis so the UI can render all four
    together, but never computes one: every value here arrived from the controller,
    because on an open-loop stage the controller's count is the only truth there is.
    A locally-accumulated total would drift silently the moment a step is dropped or
    a move is refused, which is precisely when the operator needs to be told.
    """

    WORKER_ID = "picomotor"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=PicomotorWorkerStateChanged)
        self._steps: dict[int, int] = {}

    def subscribe(self) -> None:
        self._subscribe(RequestStepPicomotor, self._on_request_step)
        self._subscribe(RequestStepPicomotorTo, self._on_request_step_to)
        self._subscribe(RequestZeroPicomotor, self._on_request_zero)
        self._subscribe(RequestPicomotorSteps, self._on_request_steps)
        # The worker reports the read-back counter after every move and every zero.
        self._subscribe_service(StepsMoved, self._on_steps_moved)

    @property
    def steps(self) -> dict[int, int]:
        """Last known counters. Empty until the first read-back lands."""
        return dict(self._steps)

    # -- imperative API (the UI uses these) --------------------------------

    def step(self, axis: int, steps: int) -> None:
        self._request(StepBy(axis=axis, steps=steps), self._on_reply)

    def step_to(self, axis: int, steps: int) -> None:
        self._request(StepTo(axis=axis, steps=steps), self._on_reply)

    def zero(self, axis: int) -> None:
        self._request(ZeroAxis(axis=axis), self._on_reply)

    def refresh(self, axes: tuple[int, ...] = ()) -> None:
        """Read the counters without moving anything.

        This is how the panel shows where the axes are at startup. Without it the
        only way to learn a position would be to move — unacceptable on a rig whose
        alignment took a session to find.
        """
        self._request(QuerySteps(axes=axes), self._on_steps_reply)

    # -- event bridge ------------------------------------------------------

    def _on_request_step(self, event: RequestStepPicomotor) -> None:
        self.step(event.axis, event.steps)

    def _on_request_step_to(self, event: RequestStepPicomotorTo) -> None:
        self.step_to(event.axis, event.steps)

    def _on_request_zero(self, event: RequestZeroPicomotor) -> None:
        self.zero(event.axis)

    def _on_request_steps(self, event: RequestPicomotorSteps) -> None:
        self.refresh(event.axes)

    # -- replies -----------------------------------------------------------

    def _on_reply(self, reply: OKReply) -> None:
        # The counter arrives separately, via StepsMoved — a move's OK only says the
        # command was accepted, not where the axis ended up.
        pass

    def _on_steps_reply(self, reply: StepsReply) -> None:
        # The IPC codec is JSON-backed and JSON object keys are strings, so a
        # dict[int, int] crosses the process boundary as dict[str, int]. Coerce here,
        # at the boundary, so nothing downstream has to know: the UI looks axes up by
        # int, and a string key silently misses every lookup (the readouts stay "—"
        # while the counters are in fact known).
        self._steps.update({int(axis): int(steps) for axis, steps in reply.steps.items()})
        self._bus.publish(PicomotorStepsChanged(steps=self.steps))

    def _on_steps_moved(self, msg: StepsMoved) -> None:
        self._steps[msg.axis] = msg.total_steps
        self._bus.publish(PicomotorStepsChanged(steps=self.steps))
