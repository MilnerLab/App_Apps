from __future__ import annotations

from app_apps.io.control_readout.esp_worker_handler import EspHandle
from app_apps.io.control_readout.picomotor_worker_handler import PicomotorHandle
from app_apps.io.control_readout.rgv_worker_handler import RgvHandle
from app_apps.io.control_readout.rotator_worker_handler import RotatorHandle
from app_apps.io.control_readout.servo_worker_handler import ServoShutterHandle
from app_apps.io.control_readout.service import ControlReadoutService
from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule


class ControlReadoutModule(BaseModule):
    name = "control_readout"

    def register(self, c: Container, ctx: AppContext) -> None:
        service = ControlReadoutService(bus=ctx.event_bus)

        handle = RotatorHandle(bus=ctx.event_bus)
        service.add_handle(handle)

        esp_handle = EspHandle(bus=ctx.event_bus)
        rgv_handle = RgvHandle(bus=ctx.event_bus)
        pico_handle = PicomotorHandle(bus=ctx.event_bus)
        servo_handle = ServoShutterHandle(bus=ctx.event_bus)
        for h in (esp_handle, rgv_handle, pico_handle, servo_handle):
            service.add_handle(h)

        c.register_instance(ControlReadoutService, service)
        c.register_instance(RotatorHandle, handle)
        c.register_instance(EspHandle, esp_handle)
        c.register_instance(RgvHandle, rgv_handle)
        c.register_instance(PicomotorHandle, pico_handle)
        c.register_instance(ServoShutterHandle, servo_handle)

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        c.get(ControlReadoutService).start()
        if ctx.status == AppStatus.CONNECTED:
            for handle_type in (RotatorHandle, EspHandle, RgvHandle, PicomotorHandle, ServoShutterHandle):
                c.get(handle_type).start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        for handle_type in (ServoShutterHandle, PicomotorHandle, RgvHandle, EspHandle, RotatorHandle):
            c.get(handle_type).pause()
        c.get(ControlReadoutService).stop()
