from __future__ import annotations

from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from camera.base.buffer import CameraBuffer, CameraMemorySpec
from camera.base.config import CameraConfig

from app_apps.io.camera.camera_worker_handler import CameraWorkerHandle
from app_apps.io.camera.service import CameraService


class CameraModule(BaseModule):
    """
    Generic, directly instantiable module for a camera device.

    Not subclassed per vendor -- every vendor difference (hardware driver,
    subprocess entry point, domain event classes, default config) is supplied
    as a constructor argument. A vendor's app_apps/io/camera_<vendor> package
    only needs an events.py and a small factory function that builds one of
    these (see app_apps/io/camera_vmi/module.py).
    """

    def __init__(
        self,
        *,
        name: str,
        entry_module: str,
        buffer_name: str,
        config: CameraConfig,
        frame_available_cls: type,
        frame_ack_cls: type,
        config_changed_cls: type,
        state_changed_cls: type,
        python_exe: str | None = None,
    ) -> None:
        self.name = name
        self._entry_module = entry_module
        self._buffer_name = buffer_name
        self._config = config
        self._frame_available_cls = frame_available_cls
        self._frame_ack_cls = frame_ack_cls
        self._config_changed_cls = config_changed_cls
        self._state_changed_cls = state_changed_cls
        self._python_exe = python_exe
        self._service: CameraService | None = None
        self._handle: CameraWorkerHandle | None = None

    def register(self, c: Container, ctx: AppContext) -> None:
        spec = CameraMemorySpec(
            self._buffer_name,
            width=self._config.width,
            height=self._config.height,
        )

        self._service = CameraService(
            bus=ctx.event_bus,
            entry_module=self._entry_module,
            python_exe=self._python_exe,
        )

        self._handle = CameraWorkerHandle(
            worker_id=self.name,
            bus=ctx.event_bus,
            spec=spec,
            config=self._config,
            frame_available_cls=self._frame_available_cls,
            frame_ack_cls=self._frame_ack_cls,
            config_changed_cls=self._config_changed_cls,
            state_changed_cls=self._state_changed_cls,
        )
        self._service.add_buffer(CameraBuffer, spec)
        self._service.add_handle(self._handle)

        # NOTE: registering by shared type only works while a single camera
        # module is active -- with two camera instances (e.g. camera_vmi and
        # a future Thorlabs module) these overwrite each other in the
        # container. Consumers that need a *specific* camera's handle should
        # be given self._handle directly (e.g. via a per-vendor factory
        # function) rather than resolving CameraWorkerHandle from the
        # container, until keyed/per-instance DI registration is added.
        c.register_instance(CameraService, self._service)
        c.register_instance(CameraWorkerHandle, self._handle)

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        self._service.start()
        if ctx.status == AppStatus.CONNECTED:
            self._handle.start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        self._handle.pause()
        self._service.stop()
