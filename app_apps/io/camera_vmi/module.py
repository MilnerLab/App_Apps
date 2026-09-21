from __future__ import annotations

from base_core.quantities.enums import Prefix
from base_core.quantities.models import Time
from camera.base.config import CameraConfig

from app_apps.io.camera.module import CameraModule
from app_apps.io.camera_vmi.events import (
    VmiCameraConfigChanged,
    VmiCameraWorkerStateChanged,
    VmiFrameAck,
    VmiFrameAvailable,
)

# Must match Devices/camera/vmi/camera_process.py's WORKER_ID.
NAME = "camera_vmi"


def build_camera_vmi_module() -> CameraModule:
    """Build the CameraModule instance for the Blackfly S VMI camera."""
    return CameraModule(
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
    )
