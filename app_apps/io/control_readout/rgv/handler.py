from __future__ import annotations

import logging

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

log = logging.getLogger(__name__)

# Travel limit of the RGV100BL, in degrees either side of home. Until 2026-07-20 this
# number existed only as a COMMENT here and nothing enforced it: `_on_request_rotate`
# accumulated unbounded increments and handed whatever came out straight to `move_to`.
# The phase loop emits a correction on every committed frame (~4/s), so a fault that
# keeps the sign constant -- a biased phase readout, an inverted CORRECTION_SIGN, a
# spectrometer that stops producing usable frames mid-move -- winds the plate steadily
# with nothing to stop it. Operators saw the plate take itself through multiple full
# turns. Whatever starts that, a rotator asked to leave its own travel range is ALWAYS
# a fault, so it is refused here: this is the last point in the chain that knows what
# the hardware can physically do.
RGV_MAX_DEG = 168.0


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
        # wrap=False: this is a position on the ±RGV_MAX_DEG stage, not a circular angle.
        # Wrapping here would be the worst possible failure -- a plate at +170° would come
        # back as -190° and the stage would drive most of a turn the WRONG way to reach it.
        want = base.Deg + event.angle.Deg
        clamped = min(max(want, -RGV_MAX_DEG), RGV_MAX_DEG)
        if clamped != want:
            # Loud, and every time: the loop is asking for travel the stage does not have,
            # which means the phase readout feeding it is wrong (or the sample drifted far
            # enough that the plate genuinely cannot follow). Silently saturating would
            # leave the loop pushing against a wall with the chart showing an error that
            # never closes -- which is exactly the symptom that is hard to diagnose.
            log.warning(
                "RGV travel limit: correction %+.3f deg from %.3f deg wants %.3f deg, "
                "outside +-%.1f deg. Clamped to %.3f. The phase loop is winding the plate "
                "-- check the fringe fit before re-running.",
                event.angle.Deg, base.Deg, want, RGV_MAX_DEG, clamped,
            )
        target = Angle(clamped, AngleUnit.DEG, wrap=False)
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
