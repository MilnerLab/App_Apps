"""Look at the VMI ion panel without the camera.

Builds the real IonViewModel and IonView, the same way CameraVMIModule's factories do,
but hands the view model the camera preview's stub handle, whose buffer serves synthetic
frames of bright spots on a ring. A timer publishes VmiFrameAvailable on the bus, so
frames take the same path as in the app: bus -> copy + ack -> detector thread ->
transformed points -> view.

    python test/ion_view_preview.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from base_core.framework.events import EventBus  # noqa: E402
from base_qt.app.dispatcher import QtDispatcher  # noqa: E402
from base_qt.ui.apply import install_ui  # noqa: E402

from app_apps.analysis.ion_detection import ContourConfig  # noqa: E402
from app_apps.io.camera_vmi.events import VmiFrameAvailable  # noqa: E402
from app_apps.io.camera_vmi.ion_settings import IonSettings  # noqa: E402
from app_apps.io.camera_vmi.ui.ion_view import IonView  # noqa: E402
from app_apps.io.camera_vmi.ui.ion_view_model import IonViewModel  # noqa: E402
from base_core.math.models import Range  # noqa: E402

from camera_vmi_view_preview import (  # noqa: E402
    FRAME_INTERVAL_MS,
    HEIGHT,
    WIDTH,
    _FakeCameraHandle,
    _synthetic_frame,
)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    install_ui(app)

    bus = EventBus()
    handle = _FakeCameraHandle()
    # Thresholds suited to the synthetic frames (Poisson background ~4, 3x3 spots >= 150);
    # ContourConfig's defaults are tuned for the real detector, not this.
    settings = IonSettings(
        contour=ContourConfig(threshold_value=100, contour_max_area=20),
        center_x=WIDTH / 2,
        center_y=HEIGHT / 2,
        analysis_zone=Range(100.0, 500.0),
    )
    vm = IonViewModel(bus, QtDispatcher(), handle, settings)  # type: ignore[arg-type]
    view = IonView(vm)
    view.resize(800, 800)
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
