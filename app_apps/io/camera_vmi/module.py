from __future__ import annotations

from base_core.framework.app.context import AppContext
from base_core.framework.di import Container
from base_core.quantities.enums import Prefix
from base_core.quantities.models import Time
from camera.base.config import PYTHON310_PATH, CameraConfig

from app_apps.io.camera.module import CameraModule
from app_apps.io.camera_vmi.events import (
    VmiCameraConfigChanged,
    VmiCameraWorkerStateChanged,
    VmiFrameAck,
    VmiFrameAvailable,
)

# Must match Devices/camera/vmi/camera_process.py's WORKER_ID.
NAME = "camera_vmi"


class CameraVMIModule(CameraModule):
    """CameraModule for the Blackfly S VMI camera, plus its live-image panel.

    Subclassed (rather than a factory around CameraModule) only to register the VMI
    view/view model; all device wiring is still the generic CameraModule's.
    """

    def __init__(self) -> None:
        super().__init__(
            name=NAME,
            entry_module="camera.vmi.camera_process",
            buffer_name=f"{NAME}_frame",
            config=CameraConfig(
                width=1224,
                height=1024,
                pixel_format="Mono8",
                exposure_time=Time(2000, Prefix.MICRO),
                gain=25.0,
            ),
            frame_available_cls=VmiFrameAvailable,
            frame_ack_cls=VmiFrameAck,
            config_changed_cls=VmiCameraConfigChanged,
            state_changed_cls=VmiCameraWorkerStateChanged,
            python_exe=PYTHON310_PATH
        )

    def register(self, c: Container, ctx: AppContext) -> None:
        super().register(c, ctx)

        from app_apps.io.camera_vmi.ui.camera_vmi_view import CameraVmiView
        from app_apps.io.camera_vmi.ui.camera_vmi_view_model import CameraVmiViewModel
        from base_qt.app.dispatcher import QtDispatcher

        # The handle is passed directly rather than resolved as CameraWorkerHandle from the
        # container: that registration is shared by every camera module (see CameraModule).
        handle = self._handle
        c.register_factory(CameraVmiViewModel, lambda c: CameraVmiViewModel(
            ctx.event_bus,
            c.get(QtDispatcher),
            handle,
        ))
        c.register_factory(CameraVmiView, lambda c: CameraVmiView(c.get(CameraVmiViewModel)))
