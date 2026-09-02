from __future__ import annotations

import logging

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from base_core.math.enums import AngleUnit
from base_core.math.models import Angle
from control_readout.ell14.messages import CurrentELL14Position, HomeELL14Rotator, RotateELL14

from app_apps.io.control_readout.ell14.events import (
    ELL14RotatorHomed,
    ELL14WorkerStateChanged,
    NewELL14Angle,
    RequestRotate,
)

log = logging.getLogger(__name__)


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
        # Best-known absolute angle. None until the worker's first read-back seeds it.
        self._current_Angle: Angle | None = None

    def subscribe(self) -> None:
        self._subscribe(RequestRotate, self._on_request_rotate)
        self._subscribe_service(CurrentELL14Position, self._on_current_position)

    def home(self) -> None:
        self._request(HomeELL14Rotator(), self._on_home_reply)

    def move_to(self, angle_deg: float) -> None:
        """Command an ABSOLUTE angle, in degrees.

        The ELL14 IPC contract is relative-only (``RotateELL14`` -> ``device.move_by``), and
        the panel is App-side work with no Devices-repo change in it, so the absolute move is
        synthesised here as ``target - current`` -- exactly the mirror of what ``RgvHandle``
        does to turn the loop's relative increments into the RGV's absolute contract.

        It therefore needs a known angle, and refuses without one rather than rotating from
        an assumed zero. The worker pushes ``CurrentELL14Position`` after every rotate, and
        home leaves the device at 0.
        """
        if self._current_Angle is None:
            log.warning("ELL14: absolute move refused, the angle has never been read. "
                        "Home the rotator first.")
            return
        delta = Angle(float(angle_deg) - self._current_Angle.Deg, AngleUnit.DEG, wrap=False)
        if delta.Deg == 0.0:
            return
        # Optimistic, so back-to-back absolute moves accumulate correctly before the
        # read-back lands. Overwritten by the true angle in _on_current_position.
        self._current_Angle = Angle(float(angle_deg), AngleUnit.DEG, wrap=False)
        self._request(RotateELL14(angle=delta), self._on_rotate_reply)

    def get_position(self) -> None:
        """Re-publish the tracked angle.

        There is no GetPosition message in the ELL14 contract, so this reports what the
        handle knows rather than interrogating the hardware. It publishes nothing when the
        angle has never been read, which is what keeps the panel's readout showing "—"
        instead of a fabricated zero.
        """
        if self._current_Angle is not None:
            self._bus.publish(NewELL14Angle(self._current_Angle))

    def _on_request_rotate(self, event: RequestRotate) -> None:
        # A RELATIVE increment. Track it optimistically so an absolute move issued straight
        # after one still lands on the right target.
        if self._current_Angle is not None:
            self._current_Angle = Angle(self._current_Angle.Deg + event.angle.Deg,
                                        AngleUnit.DEG, wrap=False)
        self._request(RotateELL14(angle=event.angle), self._on_rotate_reply)

    def _on_rotate_reply(self, reply: OKReply) -> None:
        pass

    def _on_home_reply(self, reply: OKReply) -> None:
        # device.home() zeroes the rotator's own angle tracking, so this is the one place
        # the absolute angle becomes known without a move.
        self._current_Angle = Angle(0, AngleUnit.DEG, wrap=False)
        self._bus.publish(NewELL14Angle(self._current_Angle))
        self._bus.publish(ELL14RotatorHomed())

    def _on_current_position(self, msg: CurrentELL14Position) -> None:
        self._current_Angle = msg.angle
        self._bus.publish(NewELL14Angle(self._current_Angle))
