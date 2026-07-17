from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from base_core.math.enums import AngleUnit
from base_core.math.models import Angle
from control_readout.newport_xps.rgv100bl.messages import (
    GetCurrentRGVAngle,
    HomeRGV,
    RGVAngleReply,
    RGVAngleUpdate,
    RotateRGVTo,
)

from app_apps.io.control_readout.rgv.events import (
    NewRGVAngle,
    RequestCurrentRGVAngle,
    RequestRotateRGV,
    RgvWorkerStateChanged,
)


class RgvHandle(BaseWorkerHandle):
    """Main-process handle to the RGV100BL HWP rotator.

    The phase loop (and the envelope hill-climb) emit *relative* increments — how
    far to nudge the plate, not where to put it. The RGV worker, however, only
    knows how to move to an *absolute* position (``RotateRGVTo`` → ``move_to``).
    Rather than change the shared Devices contract, this handle bridges the two:
    it keeps the plate's current absolute position (fed by the worker's read-back
    after every move) and turns each incoming increment into ``position + delta``.

    Why not a pure running total: the worker reports the true read-back angle after
    each move via ``RGVAngleUpdate``, so re-seeding from it means the tracked
    position can never silently drift from the hardware. The optimistic update in
    :meth:`_on_request_rotate` only bridges the gap until that read-back lands, so
    back-to-back corrections still accumulate correctly.
    """

    WORKER_ID = "rgv100bl"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, bus, state_event=RgvWorkerStateChanged)
        # Best-known absolute plate position. None until the first read-back seeds it.
        self._current_angle: Angle | None = None

    def subscribe(self) -> None:
        self._subscribe(RequestRotateRGV, self._on_request_rotate)
        self._subscribe(RequestCurrentRGVAngle, self._on_request_current_angle)
        # Spontaneous read-back after every move/home keeps _current_angle honest.
        self._subscribe_service(RGVAngleUpdate, self._on_angle_update)
        # Seed the position once up front so the very first correction is a true
        # relative move rather than an absolute jump from an assumed zero.
        self._request(GetCurrentRGVAngle(), self._on_angle_reply)

    def home(self) -> None:
        self._request(HomeRGV(), self._on_rotate_reply)

    def _on_request_rotate(self, event: RequestRotateRGV) -> None:
        # event.angle is a *relative* increment; translate to an absolute target
        # against the last known plate position (the worker moves absolutely).
        base = self._current_angle if self._current_angle is not None else Angle(0, AngleUnit.DEG)
        # wrap=False: this is a position on the ±168° stage, not a circular angle.
        target = Angle(base.Deg + event.angle.Deg, AngleUnit.DEG, wrap=False)
        # Optimistic: keeps back-to-back corrections accumulating before the
        # read-back lands. Overwritten by the true angle in _on_angle_update.
        self._current_angle = target
        self._request(RotateRGVTo(angle=target), self._on_rotate_reply)

    def _on_request_current_angle(self, event: RequestCurrentRGVAngle) -> None:
        self._request(GetCurrentRGVAngle(), self._on_angle_reply)

    def _on_rotate_reply(self, reply: OKReply) -> None:
        pass

    def _on_angle_update(self, msg: RGVAngleUpdate) -> None:
        self._current_angle = msg.angle
        self._bus.publish(NewRGVAngle(angle=msg.angle))

    def _on_angle_reply(self, reply: RGVAngleReply) -> None:
        self._current_angle = reply.angle
        self._bus.publish(NewRGVAngle(angle=reply.angle))
