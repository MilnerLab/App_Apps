from __future__ import annotations

from typing import ClassVar

from base_core.framework.app.app_message import AppMessage, MessageLevel
from base_core.framework.subprocess.subprocess_service import SubprocessService

from app_apps.io.control_readout.events import RotateRequested
from control_readout.elliptec.messages import Rotate
from control_readout.elliptec.rotator_worker import RotatorWorker


class ControlReadoutService(SubprocessService):
    """Main-process handle to the Control & Readout subprocess."""

    service_name: ClassVar[str] = "control_readout"

    # ------------------------------------------------------------------
    # Stream lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        super().start()
        self._publish_status(True)

    def stop(self) -> None:
        self._publish_status(False)
        super().stop()

    # ------------------------------------------------------------------
    # Rotator worker
    # ------------------------------------------------------------------

    def start_rotator(self) -> None:
        self._rotating = False
        self._rotate_sub = self._bus.subscribe(RotateRequested, self._on_rotate_requested)
        self.worker(RotatorWorker.name).start_async(
            key="control_readout.rotator.start",
            on_error=lambda exc: self._bus.publish(
                AppMessage(f"Rotator failed to start: {exc}", MessageLevel.ERROR)
            ),
        )

    def stop_rotator(self) -> None:
        self._rotate_sub()
        self.worker(RotatorWorker.name).stop()

    def _on_rotate_requested(self, event: RotateRequested) -> None:
        if self._rotating:
            return
        self._rotating = True
        self.worker(RotatorWorker.name).request_async(
            Rotate(angle_rad=event.angle_rad),
            key="control_readout.rotator.rotate",
            cancel_previous=True,
            on_success=lambda _: setattr(self, "_rotating", False),
            on_error=lambda _: setattr(self, "_rotating", False),
        )
