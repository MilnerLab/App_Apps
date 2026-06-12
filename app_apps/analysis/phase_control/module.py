from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app_apps.analysis.phase_control.service import PhaseControlService
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig
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
from app_apps.io.spectrometer.module import SpectrometerModule
from app_apps.io.spectrometer.service import SpectrometerService
from app_apps.app.service_config import ServiceConfig
from base_core.framework.app.context import AppContext
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from base_core.framework.subprocess.shared_memory.shared_memory_base_messages import base_registry
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer

_PROCESS_SCRIPT = str(
    Path(__file__).parent / "subprocess" / "phase_control_process.py"
)


class PhaseControlModule(BaseModule):
    name = "phase_control"
    requires = (SpectrometerModule, ControlReadoutModule)

    def register(self, c: Container, ctx: AppContext) -> None:
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
            spec_output=c.get(SpectrometerService).output,
            config=c.get(StabilizationConfig),
        ))

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        if not c.get(ServiceConfig).phase_control:
            return

        svc = c.get(PhaseControlService)
        svc.start()

        ctx.lifecycle.add(svc.stop)
