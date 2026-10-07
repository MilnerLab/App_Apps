from __future__ import annotations

from typing import ClassVar

from PySide6.QtCore import Signal

from app_apps.io.camera.camera_worker_handler import CameraWorkerHandle
from app_apps.io.camera_vmi.events import VmiFrameAck, VmiFrameAvailable
from base_core.framework.events import EventBus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread


class CameraVmiViewModel(PanelViewModel):
    CONSUMER_ID: ClassVar[str] = "camera_vmi_vm"

    frame_updated = Signal(object)  # frame: ndarray, (height, width)

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        camera_handle: CameraWorkerHandle,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._camera_handle = camera_handle
        camera_handle.register_consumer(self.CONSUMER_ID)
        self._sub(VmiFrameAvailable, self.on_frame)

    def on_close(self) -> None:
        self._camera_handle.unregister_consumer(self.CONSUMER_ID)
        super().on_close()

    @ui_thread
    def on_frame(self, event: VmiFrameAvailable) -> None:
        try:
            # frame() copies out of shared memory, so the emitted array stays valid after
            # the slot is acked and reused by the writer.
            frame = self._camera_handle.buffer.frame(event.slot)
            self.frame_updated.emit(frame)
        except Exception as exc:
            self._msg(f"Camera frame read error: {exc}", MessageLevel.WARNING)
        finally:
            self._bus.publish(VmiFrameAck(slot=event.slot, item_id=event.item_id, consumer_id=self.CONSUMER_ID))
