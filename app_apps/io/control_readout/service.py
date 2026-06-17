from __future__ import annotations

from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.ipc.subprocess_service import SubprocessService


class ControlReadoutService(SubprocessService):
    """
    Main-process service for the control readout subprocess.

    Hosts the rotator (ELL14 HWP), ESP301 stages, RGV100BL HWP, picomotors,
    and servo shutters. A pressure-sensor WriterWorkerHandle will be added here
    when the sensor worker is implemented.
    """

    def __init__(self, bus: EventBus, io: TaskRunner) -> None:
        super().__init__(bus, io)

    @property
    def _entry_module(self) -> str:
        return "control_readout.control_readout_process"
