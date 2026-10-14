from __future__ import annotations

from app_apps.io.camera_vmi.coordinates_vmi import CoordinatesVMI
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
                width=720,
                height=540,
                pixel_format="Mono8",
                exposure_time=Time(10, Prefix.MILLI),
                gain=40,
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

        from app_apps.io.camera_vmi.ui.camera_vmi_controls_view_model import CameraVmiControlsViewModel
        from app_apps.io.camera_vmi.ui.config_view import CameraVmiDeviceView

        # Singleton, like OscilloscopeViewModel: the Devices page holds it for the life of
        # the app, and it is not a frame consumer, so it never needs tearing down.
        c.register_singleton(CameraVmiControlsViewModel, lambda c: CameraVmiControlsViewModel(
            ctx.event_bus,
            c.get(QtDispatcher),
            handle,
        ))
        c.register_singleton(CameraVmiDeviceView, lambda c: CameraVmiDeviceView(
            c.get(CameraVmiControlsViewModel), parent=None
        ))

        from app_apps.io.camera_vmi.coordinates_vmi import CoordinatesVMI
        from app_apps.io.camera_vmi.ui.ion_view import IonView
        from app_apps.io.camera_vmi.ui.ion_view_model import IonViewModel

        # One settings instance for the module's lifetime, so the ion view keeps its
        # settings across close/reopen even though its VM is rebuilt each time.
        coords = CoordinatesVMI(
            center_x=self._config.width / 2,
            center_y=self._config.height / 2,
        )
        c.register_instance(CoordinatesVMI, coords)
        c.register_factory(IonViewModel, lambda c: IonViewModel(
            ctx.event_bus,
            c.get(QtDispatcher),
            handle,
            coords,
        ))
        c.register_factory(IonView, lambda c: IonView(c.get(IonViewModel)))
