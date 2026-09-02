from __future__ import annotations

from typing import Callable, TYPE_CHECKING

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel

from app_apps.io.control_readout.rgv.handler import RgvHandle
from app_apps.io.control_readout.rgv.events import (
    NewRGVAngle,
    RgvSpinStateChanged,
    RgvWorkerStateChanged,
)
from app_apps.io.control_readout.ui.motion_view_model import MotionViewModel
from base_qt.ui.panel_view_model import ui_thread

#: Continuous-rotation rate limits, in mechanical revolutions per second.
#:
#: The ceiling is the RGV100's own: 720 deg/s is 2 rev/s, and past it the controller
#: faults. The floor is the slowest rate that still reads as continuous rotation rather
#: than a slow drift.
#:
#: On a half-wave plate the OPTICAL modulation is four times this -- a HWP turned by theta
#: rotates the polarisation by 2*theta and shifts the relative circular phase by 4*theta --
#: so 0.5 rev/s sweeps the phase through four full cycles a second.
MIN_SPIN_HZ = 0.5
MAX_SPIN_HZ = 2.0
DEFAULT_SPIN_HZ = 0.5
DEG_PER_REV = 360.0

if TYPE_CHECKING:
    from app_apps.analysis.phase_control.service import PhaseControlService


class RgvViewModel(MotionViewModel):
    """The half-wave plate the phase loop turns — so a hand move needs an interlock.

    Moving the RGV while stabilization is running is two controllers fighting over one
    plate: the operator turns it, the loop measures the phase error that creates and turns
    it back. The required behaviour is not "move and hope" -- the loop must be STOPPED
    before the plate is touched.

    The service is injected rather than the stabilization handle, deliberately: the
    envelope worker drives the same plate, and ``PhaseControlService.stop_worker()`` stops
    whichever of the two is active. Interlocking only against the phase worker would leave
    the envelope hill-climb free to fight the operator.

    The confirmation itself is the view's job (it owns the dialog); this exposes the two
    pieces it needs -- whether anything is running, and how to stop it.
    """

    spin_state_changed = Signal(bool, float)  # spinning, rev/s

    units = "deg"
    decimals = 3
    # The RGV100BL's travel. Advisory only; the device enforces its own limits.
    limits = (-168.0, 168.0)
    default_step = 1.0

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: RgvHandle,
        phase_service: PhaseControlService,
    ) -> None:
        super().__init__(bus, dispatcher, handle, RgvWorkerStateChanged, NewRGVAngle,
                         read_position=lambda e: e.angle.Deg)
        self._phase_service = phase_service
        #: Veto called before a manual command interrupts a running spin, and before the
        #: spin itself starts. Installed by the view, which owns the dialogs.
        self.confirm_spin_override: Callable[[str], bool] | None = None
        self._sub(RgvSpinStateChanged, self._on_spin_state)

    @property
    def device_name(self) -> str:
        return "RGV100BL"

    @property
    def stabilization_running(self) -> bool:
        """True if the phase or envelope worker is actively driving this plate."""
        return self._phase_service.active_state in (WorkerStatus.RUNNING, WorkerStatus.BUSY)

    def stop_stabilization(self) -> None:
        """Stop whichever control worker is active. Call BEFORE the move, not after."""
        self._phase_service.stop_worker()
        self._msg("Stabilization stopped — the RGV is now under manual control.",
                  MessageLevel.WARNING)

    # -- continuous rotation ----------------------------------------------------------
    @property
    def spinning(self) -> bool:
        """Whether the plate is free-running, as the HANDLE sees it.

        Not the flag set by ``RgvSpinStateChanged``: that arrives through the dispatcher a
        turn of the event loop later, and every precedence check here has to be right in
        the same call that issued the command. The signal drives the rendering; this drives
        the decisions.
        """
        return self._handle.spinning

    def start_spin(self, rev_per_s: float) -> None:
        """Free-run the plate at ``rev_per_s`` revolutions per second.

        Overrides stabilization: the loop is stopped first, on the same confirmation the
        manual moves use. There is no state in which both are driving the plate.
        """
        if self.spinning:
            self.set_spin_rate(rev_per_s)
            return
        rate = self._clamp_rate(rev_per_s)
        if not self._allow_move(f"spin the plate continuously at {rate:.2f} rev/s"):
            return
        # The angle stops meaning anything the moment the plate starts turning.
        self._forget_position()
        self._handle.spin(rate * DEG_PER_REV)
        self._msg(f"RGV100BL spinning at {rate:.2f} rev/s "
                  f"({rate * DEG_PER_REV:.0f} deg/s, {4 * rate:.1f} Hz optical phase).",
                  MessageLevel.INFO)

    def stop_spin(self) -> None:
        """Ramp the plate down. Unconditional -- stopping never needs confirming."""
        if not self.spinning:
            return
        self._handle.stop_spin()
        self._msg("RGV100BL spin stopped.", MessageLevel.INFO)

    def set_spin_rate(self, rev_per_s: float) -> None:
        """Change the rate of a spin already running. A no-op when it is not."""
        if not self.spinning:
            return
        rate = self._clamp_rate(rev_per_s)
        self._handle.spin(rate * DEG_PER_REV)

    @staticmethod
    def _clamp_rate(rev_per_s: float) -> float:
        return max(MIN_SPIN_HZ, min(MAX_SPIN_HZ, float(rev_per_s)))

    @ui_thread
    def _on_spin_state(self, event: RgvSpinStateChanged) -> None:
        self.spin_state_changed.emit(event.spinning,
                                     event.velocity_deg_s / DEG_PER_REV)
        if event.error:
            # A refusal, not a stop the operator asked for. Say so loudly: the button has
            # just sprung back to "Spin" on its own, which otherwise looks like a glitch.
            self._msg(f"RGV100BL refused the spin: {event.error}", MessageLevel.ERROR)
            return
        # A spin that has ended leaves a plate at a real, if arbitrary, angle. Read it, so
        # the readout fills back in and relative moves work again without a manual Read.
        if not event.spinning:
            self.refresh()

    # -- precedence -------------------------------------------------------------------
    def refresh(self) -> None:
        if self.spinning:
            # Answerable, but not usefully: it would be a different number by the time it
            # reached the screen, and showing it implies a position the operator can act on.
            self._msg("The RGV is spinning — stop it to read a position.",
                      MessageLevel.WARNING)
            return
        super().refresh()

    def _allow_move(self, description: str) -> bool:
        """Manual commands outrank a running spin, and a spin outranks stabilization.

        Both interruptions confirm first and act only on Yes, and both stop the thing they
        are overriding BEFORE the new command goes out -- there is no window in which two
        sources are driving the plate.
        """
        if self.spinning:
            if self.confirm_spin_override is None or not self.confirm_spin_override(description):
                return False
            self._handle.stop_spin()
            self._msg("Spin stopped — the RGV is now under manual control.",
                      MessageLevel.WARNING)
        return super()._allow_move(description)
