from __future__ import annotations

from app_apps.io.control_readout.buffer import PressureMemorySpec
from app_apps.io.control_readout.rotator_worker_handler import RotatorHandle
from app_apps.io.control_readout.service import ControlReadoutService
from base_core.framework.app.context import AppContext
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule


class ControlReadoutModule(BaseModule):
    name = "control_readout"

    def register(self, c: Container, ctx: AppContext) -> None:
        spec = PressureMemorySpec("control_readout_pressure")
        c.register_instance(PressureMemorySpec, spec)

        service = ControlReadoutService(
            bus=ctx.event_bus,
            io=c.get(TaskRunner),
            spec=spec,
        )

        handle = RotatorHandle(service=service, bus=ctx.event_bus)
        service.add_handle(handle)

        c.register_instance(ControlReadoutService, service)
        c.register_instance(RotatorHandle, handle)

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        service = c.get(ControlReadoutService)
        handle = c.get(RotatorHandle)
        service.start()
        handle.start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        handle = c.get(RotatorHandle)
        service = c.get(ControlReadoutService)
        handle.stop()
        service.stop()
