from __future__ import annotations

import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app_apps.analysis.phase_control.domain.phase_stabilization_config import StabilizationConfig
from app_apps.analysis.phase_control.service import PhaseControlService
from app_apps.analysis.phase_control.subprocess.messages import (
    ConfigSynced,
    CorrectionAvailable,
    Reset,
    SetStabilizationConfig,
    SetEnvelopeConfig,
    SetPaused,
)
_ALL_MESSAGES = (ConfigSynced, CorrectionAvailable, Reset, SetStabilizationConfig, SetEnvelopeConfig, SetPaused)
from app_apps.io.control_readout.module import ControlReadoutModule
from app_apps.io.control_readout.service import ControlReadoutService
from app_apps.io.spectrometer.module import SpectrometerModule
from base_core.framework.app.app_message import AppMessage, MessageLevel
from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.app.service_status import ServiceStatus
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from base_core.framework.subprocess.shared_memory.shared_buffer_coordinator import (
    SharedBufferCoordinator,
)
from base_core.framework.subprocess.shared_memory.shared_memory_base_messages import base_registry
from base_core.framework.subprocess.worker_protocol import WorkerError
from elliptec.messages import Rotate
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer

_PROCESS_SCRIPT = str(
    Path(__file__).parent / "subprocess" / "phase_control_process.py"
)


class PhaseControlModule(BaseModule):
    name = "phase_control"
    requires = (SpectrometerModule, ControlReadoutModule)

    def register(self, c: Container, ctx: AppContext) -> None:
        coord = c.get(SharedBufferCoordinator)
        coord.register_consumer("phase_tracking")
        coord.register_consumer("envelope")

        c.register_singleton(StabilizationConfig, lambda _: StabilizationConfig())

        c.register_singleton(PhaseControlService, lambda c: PhaseControlService(
            io=TaskRunner(
                ThreadPoolExecutor(max_workers=2, thread_name_prefix="phase-control-io")
            ),
            endpoint=JsonlSubprocessEndpoint(
                argv=[sys.executable, "-u", _PROCESS_SCRIPT],
                registry=base_registry().extend(*_ALL_MESSAGES),
            ),
            bus=ctx.event_bus,
            spec_buffer=c.get(SharedSpectrumBuffer),
            spec_coordinator=c.get(SharedBufferCoordinator),
            config=c.get(StabilizationConfig),
        ))

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        if ctx.status == AppStatus.OFFLINE:
            return

        svc = c.get(PhaseControlService)

        _PHASE_WORKERS = {"phase_tracking", "envelope"}

        def _on_worker_error(msg: WorkerError) -> None:
            if msg.worker_name not in _PHASE_WORKERS:
                return
            detail = f"crashed ({msg.worker_name}): {msg.error}"
            ctx.event_bus.publish(AppMessage(f"Phase control {detail}", MessageLevel.ERROR))
            ctx.event_bus.publish(ServiceStatus(PhaseControlService.service_name, False, detail))

        ctx.lifecycle.add(ctx.event_bus.subscribe(WorkerError, _on_worker_error))
        svc.start()

        rotator = c.get(ControlReadoutService).worker("rotator")

        def _on_correction(event: CorrectionAvailable) -> None:
            rotator.send(Rotate(angle_rad=event.correction_deg * math.pi / 180))

        unsub = ctx.event_bus.subscribe(CorrectionAvailable, _on_correction, source="phase_control")
        ctx.lifecycle.add(unsub)
        ctx.lifecycle.add(svc.stop)
