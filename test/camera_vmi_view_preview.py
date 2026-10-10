"""Look at the VMI camera panel without the camera.

Builds the real CameraVmiViewModel and CameraVmiView, the same way CameraVMIModule's
factories do, but hands the view model a stub handle whose buffer serves synthetic frames.
A timer publishes VmiFrameAvailable on the bus, so frames take the same path as in the
app: bus -> on_frame -> buffer.frame -> frame_updated -> view.

    python test/camera_vmi_view_preview.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from base_core.framework.events import EventBus  # noqa: E402
from base_qt.app.dispatcher import QtDispatcher  # noqa: E402
from base_qt.ui.apply import install_ui  # noqa: E402
from camera.base.config import CameraConfig  # noqa: E402

from app_apps.io.camera_vmi.events import VmiFrameAvailable  # noqa: E402
from app_apps.io.camera_vmi.ui.camera_vmi_view import CameraVmiView  # noqa: E402
from app_apps.io.camera_vmi.ui.camera_vmi_view_model import CameraVmiViewModel  # noqa: E402

# Matches CameraVMIModule's CameraConfig.
WIDTH = 1224
HEIGHT = 1024
FRAME_INTERVAL_MS = 33
HITS_PER_FRAME = 40

_rng = np.random.default_rng()


def _synthetic_frame() -> np.ndarray:
    """Dim noisy background with bright 3x3 spots on a ring, roughly like VMI ion hits."""
    frame = _rng.poisson(4, size=(HEIGHT, WIDTH)).astype(np.uint8)
    cy, cx = HEIGHT / 2, WIDTH / 2
    angles = _rng.uniform(0, 2 * np.pi, HITS_PER_FRAME)
    radii = _rng.normal(300, 100, HITS_PER_FRAME)
    for y, x in zip(cy + radii * np.sin(angles), cx + radii * np.cos(angles)):
        y, x = int(y), int(x)
        frame[max(y - 1, 0):y + 2, max(x - 1, 0):x + 2] = _rng.integers(150, 256)
    return frame


class _FakeCameraBuffer:
    def __init__(self) -> None:
        self.latest = _synthetic_frame()

    def frame(self, slot: int) -> np.ndarray:
        return self.latest.copy()


class _FakeCameraHandle:
    """Only what CameraVmiViewModel touches on a CameraWorkerHandle."""

    def __init__(self) -> None:
        self.buffer = _FakeCameraBuffer()
        self.config = CameraConfig(width=WIDTH, height=HEIGHT)

    def register_consumer(self, consumer_id: str) -> None:
        pass

    def unregister_consumer(self, consumer_id: str) -> None:
        pass


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    install_ui(app)

    bus = EventBus()
    handle = _FakeCameraHandle()
    vm = CameraVmiViewModel(bus, QtDispatcher(), handle)  # type: ignore[arg-type]
    view = CameraVmiView(vm)
    view.resize(900, 700)
    view.show()

    item_id = 0

    def publish_frame() -> None:
        nonlocal item_id
        item_id += 1
        handle.buffer.latest = _synthetic_frame()
        bus.publish(VmiFrameAvailable(slot=item_id % 2, item_id=item_id, timestamp_ns=time.time_ns()))

    timer = QTimer()
    timer.timeout.connect(publish_frame)
    timer.start(FRAME_INTERVAL_MS)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
