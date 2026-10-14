from __future__ import annotations

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.device_panel_view_model import DevicePanelViewModel
from base_qt.ui.panel_view_model import ui_thread
from camera.base.config import CameraConfig

from app_apps.io.camera.camera_worker_handler import CameraWorkerHandle
from app_apps.io.camera_vmi.events import VmiCameraConfigChanged, VmiCameraWorkerStateChanged


class CameraVmiControlsViewModel(DevicePanelViewModel):
    """Start/stop and settings for the VMI camera, for the Devices page. No image.

    Deliberately not a shared-memory consumer, same as OscilloscopeViewModel: the live
    image belongs to CameraVmiView, and a second consumer here would make every frame
    wait on this page's ack for no gain.
    """

    worker_state_changed = Signal(object)  # emits WorkerStatus
    #: The config was written back. The Devices page and the camera panel's Config popout
    #: both bind forms to the handle's one config object, so whichever applied, the other
    #: has to re-read.
    config_updated = Signal()

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: CameraWorkerHandle,
    ) -> None:
        super().__init__(bus, dispatcher, handle)
        self._handle = handle
        self._sub(VmiCameraWorkerStateChanged, self._on_state_changed)
        self._sub(VmiCameraConfigChanged, self._on_config_changed)

    @property
    def config(self) -> CameraConfig:
        """The handle's config object itself: forms edit it in place, then apply_config()."""
        return self._handle.config

    @property
    def worker_status(self) -> WorkerStatus:
        return self._handle.state

    def apply_config(self) -> None:
        # Published rather than calling handle.set_config(), as in CameraVmiViewModel: the
        # handle subscribes to this and pushes the config.
        self._bus.publish(VmiCameraConfigChanged())

    @ui_thread
    def _on_state_changed(self, _: VmiCameraWorkerStateChanged) -> None:
        self.worker_state_changed.emit(self._handle.state)

    @ui_thread
    def _on_config_changed(self, _: VmiCameraConfigChanged) -> None:
        self.config_updated.emit()

    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def resume(self) -> None:
        self._handle.resume()

    def stop(self) -> None:
        self._handle.stop()
