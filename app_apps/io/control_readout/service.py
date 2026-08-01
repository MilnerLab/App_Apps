from __future__ import annotations

import logging
import threading

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.subprocess_service import SubprocessService
from control_readout.messages import ReleaseHardware

log = logging.getLogger(__name__)


class ControlReadoutService(SubprocessService):
    """
    Main-process service for the control readout subprocess.

    Hosts the rotator (ELL14 HWP), the three ESP301 linear stages (FMS300PP,
    MFA-CC, UTS150CC), the RGV100BL HWP, and the mirror picomotors (8742).
    Servo shutters are still pending; a pressure-sensor WriterWorkerHandle will
    be added here when the sensor worker is implemented.
    """

    def __init__(self, bus: EventBus) -> None:
        super().__init__(bus)

    @property
    def _entry_module(self) -> str:
        return "control_readout.control_readout_process"

    def release_hardware(self, timeout: float = 12.0) -> None:
        """Ask the subprocess to disconnect its controllers (close COM7 etc.) and
        block until it confirms, *before* stop() hard-kills it.

        This is the graceful half of shutdown (defect G19): stop() on Windows is an
        uncatchable ``TerminateProcess``, so without this the subprocess never closes
        its serial ports and COM7 is left to an abrupt OS-reclaimed close — the class
        of action that wedged the ESP301's USB bridge. Call this immediately before
        stop().

        Best-effort and never raises: if the subprocess is not running, the send
        fails (e.g. a dead pipe), or it does not reply within ``timeout``, we log and
        return so the caller still proceeds to stop(). ``timeout`` must exceed the
        subprocess's worst case — it disconnects each controller under that
        controller's IO lock (up to ``lock_timeout`` per controller), so budget
        ``n_controllers * lock_timeout`` plus margin (2 controllers * 5 s here)."""
        connector = self.connector
        if connector is None or not self.is_running:
            return
        done = threading.Event()
        try:
            connector.request(
                ReleaseHardware(),
                on_reply=lambda _r: done.set(),
                on_error=lambda _r: done.set(),
            )
        except Exception:
            log.exception(
                "ControlReadoutService: failed to send hardware-release request; "
                "proceeding to terminate (COM7 may close abruptly)")
            return
        if not done.wait(timeout):
            log.warning(
                "ControlReadoutService: hardware release not confirmed within %.1fs; "
                "proceeding to terminate (COM7 may close abruptly)", timeout,
            )
