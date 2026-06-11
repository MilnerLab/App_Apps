from __future__ import annotations

from typing import ClassVar

from base_core.framework.app.app_message import AppMessage, MessageLevel
from base_core.framework.subprocess.subprocess_service import SubprocessService

from app_apps.io.control_readout.events import RotateRequested
from control_readout.elliptec.messages import Rotate


class ControlReadoutService(SubprocessService):
    """
    Main-process handle to the Control & Readout subprocess.

    Subscribes to RotateRequested events from the event bus and forwards them
    to the rotator worker, dropping requests that arrive while one is in flight.
    """

    service_name: ClassVar[str] = "control_readout"
    WORKER_ROTATOR: ClassVar[str] = "rotator"

    def start(self) -> None:
        super().start()
        self._rotating = False
        self._rotate_sub = self._bus.subscribe(RotateRequested, self._on_rotate_requested)
        self.worker(self.WORKER_ROTATOR).start_async(
            key="control_readout.rotator.start",
            on_error=lambda exc: self._bus.publish(
                AppMessage(f"Rotator failed to start: {exc}", MessageLevel.ERROR)
            ),
        )
        self._publish_status(True)

    def stop(self) -> None:
        self._publish_status(False)
        self._rotate_sub()
        self.worker(self.WORKER_ROTATOR).stop()
        super().stop()

    def _on_rotate_requested(self, event: RotateRequested) -> None:
        if self._rotating:
            return
        self._rotating = True
        self.worker(self.WORKER_ROTATOR).request_async(
            Rotate(angle_rad=event.angle_rad),
            key="control_readout.rotator.rotate",
            cancel_previous=True,
            on_success=lambda _: setattr(self, "_rotating", False),
            on_error=lambda _: setattr(self, "_rotating", False),
        )
