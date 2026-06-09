from __future__ import annotations

from base_core.framework.subprocess.subprocess_service import SubprocessService


class ControlReadoutService(SubprocessService):
    """
    Main-process handle to the Control & Readout subprocess.

    Currently hosts the rotator worker. Pressure sensor workers and their
    shared-memory buffers will be added as OutputBufferHandles here.

    Workers are accessed by name:
        svc.worker("rotator")  ->  WorkerHandle
    """
