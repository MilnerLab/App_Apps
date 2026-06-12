from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.ipc.message import OKReply
from base_core.ipc.worker_handle import BaseWorkerHandle
from spm_002.config import SpectrometerConfig
from spm_002.messages import SetSpectrometerConfig

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from base_core.ipc.subprocess_service import SubprocessService


class SpectrometerWorkerHandle(BaseWorkerHandle):
    """
    Main-process handle to SpectrometerWorker.

    Exposes typed wrappers for all commands the main process can send to
    the spectrometer worker. Start/stop/reset are inherited from BaseWorkerHandle.

    Usage (from module or UI layer):
        handle.start()                      # begins acquisition
        handle.stop()                       # pauses acquisition
        handle.set_config(new_config)       # updates hardware settings live
    """

    WORKER_ID = "spectrometer"

    def __init__(self, service: SubprocessService, bus: EventBus) -> None:
        super().__init__(self.WORKER_ID, service, bus)

    def set_config(self, config: SpectrometerConfig) -> None:
        """Send a new SpectrometerConfig to the subprocess and apply it to the hardware."""
        self._request(
            SetSpectrometerConfig(config=config),
            self._on_set_config_reply,
        )

    def _on_set_config_reply(self, reply: OKReply) -> None:
        pass
