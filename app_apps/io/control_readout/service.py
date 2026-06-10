from __future__ import annotations

from typing import Callable, Optional

from base_core.framework.subprocess.subprocess_service import SubprocessService


class ControlReadoutService(SubprocessService):
    """
    Main-process handle to the Control & Readout subprocess.

    Currently hosts the rotator worker. Pressure sensor workers and their
    shared-memory buffers will be added via WorkerHandle.with_output() here.

    Workers are accessed by name:
        svc.worker("rotator")  ->  WorkerHandle
    """

    def start(self, *, on_worker_error: Optional[Callable[[BaseException], None]] = None) -> None:
        super().start()
        self.worker("rotator").start_async(
            key="control_readout.rotator.start",
            on_error=on_worker_error,
        )

    def stop(self) -> None:
        self.worker("rotator").stop()
        super().stop()
