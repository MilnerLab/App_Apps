from __future__ import annotations

from collections import deque
from enum import Enum
from typing import ClassVar

import numpy as np
from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.math.models import Points, Range
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.app_message import MessageLevel
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread

from app_apps.io.camera.camera_worker_handler import CameraWorkerHandle
from app_apps.io.camera_vmi.events import VmiFrameAck, VmiFrameAvailable
from app_apps.io.camera_vmi.ion_detector import IonDetector, IonFrameResult
from app_apps.io.camera_vmi.ion_settings import IonSettings


class IonDisplayMode(Enum):
    CURRENT = "current"          # scatter of the latest frame's ions
    ROLLING = "rolling"          # scatter of the last rolling_frames frames
    ACCUMULATED = "accumulated"  # 2D histogram of every ion since the last clear


class IonViewModel(PanelViewModel):
    """Detected ions from the VMI camera, transformed as C2TScanData would transform them.

    Its own consumer of the camera handle, independent of CameraVmiView: it needs only
    the camera subprocess running. Detection runs on IonDetector's thread; rolling and
    accumulated state live here, on the UI thread, and are fed every frame whatever the
    display mode, so switching modes shows history rather than starting empty.
    """

    CONSUMER_ID: ClassVar[str] = "camera_vmi_ion_vm"

    points_updated = Signal(object, object)  # x, y: ndarrays to scatter
    image_updated = Signal(object, object)   # histogram (x-major), extent: Range[float]
    stats_updated = Signal(object)           # IonFrameResult of the latest frame
    config_updated = Signal()                # settings applied; views reload

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        camera_handle: CameraWorkerHandle,
        settings: IonSettings,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._camera_handle = camera_handle
        self._settings = settings
        self._mode = IonDisplayMode.CURRENT
        self._last: Points | None = None
        self._rolling: deque[Points] = deque(maxlen=settings.rolling_frames)
        self._hist: np.ndarray | None = None
        self._hist_extent = settings.analysis_zone.max

        self._detector = IonDetector(settings.snapshot(), self._on_result)
        camera_handle.register_consumer(self.CONSUMER_ID)
        self._sub(VmiFrameAvailable, self._on_frame)

    @property
    def settings(self) -> IonSettings:
        """The settings object itself: forms edit it in place, then apply_config()."""
        return self._settings

    @property
    def mode(self) -> IonDisplayMode:
        return self._mode

    def set_mode(self, mode: IonDisplayMode) -> None:
        self._mode = mode
        self._emit_display()

    def clear(self) -> None:
        self._last = None
        self._rolling.clear()
        self._hist = None
        self._emit_display()

    def apply_config(self) -> None:
        self._detector.set_params(self._settings.snapshot())
        # Old history is in the previous coordinate frame -- drop it rather than mix frames.
        self._rolling = deque(maxlen=self._settings.rolling_frames)
        self._hist = None
        self._hist_extent = self._settings.analysis_zone.max
        self._last = None
        self.config_updated.emit()
        self._emit_display()

    def on_close(self) -> None:
        self._detector.stop()
        self._camera_handle.unregister_consumer(self.CONSUMER_ID)
        super().on_close()

    # -- frames in: bus thread ---------------------------------------------------------

    def _on_frame(self, event: VmiFrameAvailable) -> None:
        # Deliberately not @ui_thread: every consumer must ack before the camera's next
        # frame is handed out, so copy and ack here, at once, and leave the slow part to
        # the detector thread. A busy UI then never holds up the camera.
        frame = None
        try:
            frame = self._camera_handle.buffer.frame(event.slot)
        except Exception as exc:
            self._msg(f"Ion view frame read error: {exc}", MessageLevel.WARNING)
        finally:
            self._bus.publish(VmiFrameAck(slot=event.slot, item_id=event.item_id, consumer_id=self.CONSUMER_ID))
        if frame is not None:
            self._detector.submit(frame, event.item_id)

    # -- results in: detector thread -> UI thread --------------------------------------

    @ui_thread
    def _on_result(self, result: IonFrameResult) -> None:
        pts = result.points
        self._last = pts
        self._rolling.append(pts)
        self._accumulate(pts)
        self.stats_updated.emit(result)
        self._emit_display()

    def _accumulate(self, pts: Points) -> None:
        # 1 px bins over the zone's bounding square, in transformed coordinates.
        r = self._hist_extent
        n_bins = max(1, int(np.ceil(2 * r)))
        h, _, _ = np.histogram2d(pts.x, pts.y, bins=n_bins, range=[[-r, r], [-r, r]])
        self._hist = h if self._hist is None else self._hist + h

    def _emit_display(self) -> None:
        if self._mode is IonDisplayMode.ACCUMULATED:
            r = self._hist_extent
            if self._hist is None:
                n_bins = max(1, int(np.ceil(2 * r)))
                self._hist = np.zeros((n_bins, n_bins))
            self.image_updated.emit(self._hist, Range(-r, r))
            return

        if self._mode is IonDisplayMode.ROLLING:
            batches = list(self._rolling)
        else:
            batches = [self._last] if self._last is not None else []
        if batches:
            x = np.concatenate([p.x for p in batches])
            y = np.concatenate([p.y for p in batches])
        else:
            x = y = np.empty(0)
        self.points_updated.emit(x, y)
