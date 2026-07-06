from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.subprocess_service import SubprocessService


class ControlReadoutService(SubprocessService):
    """
    Main-process service for the control readout subprocess.

    Hosts the rotator (ELL14 HWP), the three ESP301 linear stages (FMS300PP,
    MFA-CC, UTS150CC), and the RGV100BL HWP. Picomotors and servo shutters are
    still pending; a pressure-sensor WriterWorkerHandle will be added here
    when the sensor worker is implemented.
    """

    def __init__(self, bus: EventBus) -> None:
        super().__init__(bus)

    @property
    def _entry_module(self) -> str:
        return "control_readout.control_readout_process"
