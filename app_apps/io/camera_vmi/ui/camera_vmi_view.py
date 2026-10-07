from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QPen
from PySide6.QtWidgets import QWidget

from base_qt.ui.panel import Panel
from app_apps.io.camera_vmi.ui.camera_vmi_view_model import CameraVmiViewModel


class CameraVmiView(Panel):
    def __init__(self, vm: CameraVmiViewModel, parent: QWidget | None = None) -> None:
        super().__init__("VMI Camera", vm, parent)

    def setup(self) -> None:
        self._glw = pg.GraphicsLayoutWidget()

        self._vb = self._glw.addViewBox(row=0, col=0)
        self._vb.setAspectLocked(True)

        #Testing out the pyqtgraph plots and labels
        qaxis = pg.AxisItem('left',pen=QPen())
        self._vb.addItem(qaxis)
        # Row-major per item rather than via pg.setConfigOptions: that option is global and
        # would flip every other image in the app.
        self._img_item = pg.ImageItem(axisOrder="row-major")
        lut = pg.colormap.get("plasma").getLookupTable(0.0, 1.0, 256)
        self._img_item.setLookupTable(lut)
        self._vb.addItem(self._img_item)

        self.body_layout.addWidget(self._glw, stretch=1)

        self._connect(self.vm.frame_updated, self._on_frame_updated)

    def _on_frame_updated(self, frame: np.ndarray) -> None:
        # Fixed levels at the full range of the pixel type, so brightness on screen tracks
        # the signal instead of being renormalised every frame.
        self._img_item.setImage(frame, autoLevels=False, levels=(0, np.iinfo(frame.dtype).max))
        # Auto-range until the user pans or zooms, which turns it off.
        if self._vb.autoRangeEnabled()[0]:
            self._vb.autoRange()
