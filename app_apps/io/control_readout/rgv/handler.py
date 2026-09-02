from __future__ import annotations

import logging

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import ErrorReply, OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from base_core.math.enums import AngleUnit
from base_core.math.models import Angle
from control_readout.newport_xps.rgv100bl.messages import (
    GetCurrentRGVAngle,
    HomeRGV,
    RGVAngleReply,
    RGVAngleUpdate,
    RGVSpinStateUpdate,
    RotateRGVTo,
    SpinRGV,
    StopSpinRGV,
)

from app_apps.io.control_readout.rgv.events import (
    NewRGVAngle,
    RequestCurrentRGVAngle,
    RequestRotateRGV,
    RequestSpinRGV,
    RequestStopSpinRGV,
    RgvSpinStateChanged,
    RgvWorkerStateChanged,
)


log = logging.getLogger(__name__)


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
        # True while the plate is free-running. Everything that reads or commands a
        # position consults this: a spinning plate has no position worth tracking, and a
        # tracked position that keeps its last pre-spin value is worse than none at all --
        # the next relative correction would be computed against an angle that is now
        # hundreds of degrees stale.
        self._spinning = False

    def subscribe(self) -> None:
        self._subscribe(RequestRotateRGV, self._on_request_rotate)
        self._subscribe(RequestCurrentRGVAngle, self._on_request_current_angle)
        # Spontaneous read-back after every move/home keeps _current_angle honest.
        self._subscribe_service(RGVAngleUpdate, self._on_angle_update)
        self._subscribe_service(RGVSpinStateUpdate, self._on_spin_update)
        self._subscribe(RequestSpinRGV, self._on_request_spin)
        self._subscribe(RequestStopSpinRGV, self._on_request_stop_spin)
        # Seed the position once up front so the very first correction is a true
        # relative move rather than an absolute jump from an assumed zero.
        self._request(GetCurrentRGVAngle(), self._on_angle_reply)

    def home(self) -> None:
        self._request(HomeRGV(), self._on_rotate_reply)

    def move_to(self, angle_deg: float) -> None:
        """Command an ABSOLUTE plate angle, in degrees.

        The device panel needs this; the loop does not and never calls it. Kept separate
        from the RequestRotateRGV path on purpose -- that one exists to translate the loop's
        relative increments, and routing an absolute target through it would add the target
        to the current position.
        """
        # wrap=False: this is a position on the +-168 deg stage, not a circular angle.
        target = Angle(float(angle_deg), AngleUnit.DEG, wrap=False)
        self._current_angle = target
        self._request(RotateRGVTo(angle=target), self._on_rotate_reply)

    def get_position(self) -> None:
        self._request(GetCurrentRGVAngle(), self._on_angle_reply)

    # -- continuous rotation ------------------------------------------------------- #
    @property
    def spinning(self) -> bool:
        return self._spinning

    def spin(self, velocity_deg_s: float) -> None:
        """Start or re-rate free-running rotation. Sign sets the direction.

        The position goes UNKNOWN immediately rather than when the first spin read-back
        lands: from the instant the command goes out, the tracked angle is wrong, and a
        relative move synthesised from it would be a large blind jump.
        """
        self._current_angle = None
        self._spinning = True
        self._request(SpinRGV(velocity_deg_s=float(velocity_deg_s)),
                      self._on_rotate_reply, self._on_spin_error)
        self._bus.publish(RgvSpinStateChanged(spinning=True,
                                              velocity_deg_s=float(velocity_deg_s)))

    def _on_spin_error(self, reply: ErrorReply) -> None:
        """Undo the optimistic announcement when the controller refuses the spin.

        Without this the rejection is invisible and actively harmful: the panel reads
        "spinning", the tracked angle stays unknown, and -- because a spin outranks the
        control loop -- every stabilization correction is silently swallowed by a plate
        that is not turning. The rollback republishes so the toggle and the readout come
        back, and carries the controller's text so the reason reaches the operator.
        """
        log.error("RGV: the controller refused the spin: %s", reply.error)
        self._spinning = False
        self._bus.publish(RgvSpinStateChanged(spinning=False, velocity_deg_s=0.0,
                                              error=str(reply.error)))
        # The plate never moved, so its pre-spin angle is still valid -- read it back.
        self._request(GetCurrentRGVAngle(), self._on_angle_reply)

    def stop_spin(self) -> None:
        """Ramp the plate to a stop. The worker reports the settled angle afterwards."""
        self._spinning = False
        self._request(StopSpinRGV(), self._on_rotate_reply)
        self._bus.publish(RgvSpinStateChanged(spinning=False, velocity_deg_s=0.0))

    def _on_request_spin(self, event: RequestSpinRGV) -> None:
        self.spin(event.velocity_deg_s)

    def _on_request_stop_spin(self, event: RequestStopSpinRGV) -> None:
        self.stop_spin()

    def _on_spin_update(self, msg: RGVSpinStateUpdate) -> None:
        # The worker is the authority: it also ends a spin on its own (a pause, a stop, or
        # a position command arriving from anywhere), and this is how that becomes visible
        # here rather than leaving the handle believing the plate is still turning.
        self._spinning = bool(msg.spinning)
        self._bus.publish(RgvSpinStateChanged(spinning=self._spinning,
                                              velocity_deg_s=float(msg.velocity_deg_s)))

    def _on_request_rotate(self, event: RequestRotateRGV) -> None:
        if self._spinning:
            # A control loop is correcting a free-running plate. Stop the spin -- an
            # explicit command outranks it -- but DROP this correction rather than apply
            # it: the increment is relative to a position that is no longer known, so the
            # only honest target is "nowhere". The loop re-measures and corrects next
            # cycle, by which time the read-back has re-seeded the position.
            log.warning("RGV: correction arrived while spinning; stopping the spin and "
                        "dropping this increment (position unknown)")
            self.stop_spin()
            return
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
        if self._spinning:
            # Sampled off a turning plate: true when read, wrong by the time it is used.
            return
        self._current_angle = msg.angle
        self._bus.publish(NewRGVAngle(angle=msg.angle))

    def _on_angle_reply(self, reply: RGVAngleReply) -> None:
        if self._spinning:
            return
        self._current_angle = reply.angle
        self._bus.publish(NewRGVAngle(angle=reply.angle))
