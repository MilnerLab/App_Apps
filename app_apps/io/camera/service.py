from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.subprocess_service import SubprocessService

class CameraService(SubprocessService):
    """
    Main-process service for a camera subprocess.

    Generic across camera vendors: entry_module points at whichever concrete
    subprocess entry point (e.g. "camera.vmi.camera_process") hosts that
    vendor's Camera driver. Buffer ownership and slot coordination live in
    CameraWorkerHandle.
    """

    def __init__(
        self,
        bus: EventBus,
        entry_module: str,
        python_exe: str | None = None,
    ) -> None:
        super().__init__(bus, python_exe)
        self._entry_module_path = entry_module
        
        
    @property
    def _entry_module(self) -> str:
        return self._entry_module_path
