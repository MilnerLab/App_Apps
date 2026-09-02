from __future__ import annotations

from typing import TYPE_CHECKING

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel

from app_apps.io.control_readout.rgv.handler import RgvHandle
from app_apps.io.control_readout.rgv.events import NewRGVAngle, RgvWorkerStateChanged
from app_apps.io.control_readout.ui.motion_view_model import MotionViewModel

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
