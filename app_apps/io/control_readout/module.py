from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from app_apps.io.control_readout.service import ControlReadoutService
from base_core.framework.app.app_message import AppMessage, MessageLevel
from base_core.framework.app.context import AppContext
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from base_core.framework.subprocess.shared_memory.shared_memory_base_messages import base_registry
from base_core.framework.subprocess.worker_protocol import WorkerError
from elliptec.messages import HomeRotator, Rotate, RotatorHomed, RotatorMoved


class ControlReadoutModule(BaseModule):
    name = "control_readout"
    requires = ()

    def register(self, c: Container, ctx: AppContext) -> None:
        c.register_singleton(ControlReadoutService, lambda c: ControlReadoutService(
            io=TaskRunner(
                ThreadPoolExecutor(max_workers=2, thread_name_prefix="control-readout-io")
            ),
            endpoint=JsonlSubprocessEndpoint(
                argv=[sys.executable, "-u", "-m", "control_readout.control_readout_process"],
                registry=base_registry().extend(Rotate, HomeRotator, RotatorMoved, RotatorHomed),
            ),
            bus=ctx.event_bus,
        ))

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        svc = c.get(ControlReadoutService)

        def _on_worker_error(msg: WorkerError) -> None:
            if msg.worker_name != "rotator":
                return
            ctx.event_bus.publish(AppMessage(
                f"Rotator crashed: {msg.error}", MessageLevel.ERROR
            ))

        def _on_start_error(exc: BaseException) -> None:
            ctx.event_bus.publish(AppMessage(
                f"Rotator failed to start: {exc}", MessageLevel.ERROR
            ))

        ctx.lifecycle.add(ctx.event_bus.subscribe(WorkerError, _on_worker_error))
        svc.start(on_worker_error=_on_start_error)
        ctx.lifecycle.add(svc.stop)
