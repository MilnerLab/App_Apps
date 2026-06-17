from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.subprocess_service import SubprocessService


class SpectrometerService(SubprocessService):
    """
    Main-process service for the spectrometer subprocess.

    Runs the subprocess using the 32-bit Python interpreter so PhotonSpectr.dll (x86)
    can be loaded. Buffer ownership and slot coordination live in SpectrometerWorkerHandle.
    """

    def __init__(
        self,
        bus: EventBus,
        python_exe: str | None = None,
    ) -> None:
        super().__init__(bus, python_exe)

    @property
    def _entry_module(self) -> str:
        return "spm_002.spectrometer_process"
