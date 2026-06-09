from __future__ import annotations

from base_core.framework.subprocess.subprocess_service import SubprocessService


class ControlReadoutService(SubprocessService):
    """
    Main-process handle to the Control & Readout subprocess.

    Currently hosts the rotator worker. Pressure sensor workers and their
    shared-memory buffers will be added via WorkerHandle.with_output() here.

    Workers are accessed by name:
        svc.worker("rotator")  ->  WorkerHandle
    """

    def start(self) -> None:
        super().start()
        self.worker("rotator").start_async(key="control_readout.rotator.start")

    def stop(self) -> None:
        self.worker("rotator").stop()
        super().stop()
