from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QHBoxLayout, QLabel, QPushButton, QWidget

from base_core.math.models import Range
from base_qt.ui.panel import Panel
from base_qt.ui.segmented_control import SegmentedControl

from app_apps.io.camera_vmi.ion_detector import IonFrameResult
from app_apps.io.camera_vmi.ui.ion_config_view import IonConfigView
from app_apps.io.camera_vmi.ui.ion_view_model import IonDisplayMode, IonViewModel


class IonView(Panel):
    """Transformed ion positions on a ViewBox, origin at the configured center."""

    def __init__(self, vm: IonViewModel, parent: QWidget | None = None) -> None:
        super().__init__("VMI Ions", vm, parent)

    def setup(self) -> None:
        self._glw = pg.GraphicsLayoutWidget()
        self._vb = self._glw.addViewBox(row=0, col=0)
        self._vb.setAspectLocked(True)

        # Default (col-major) axis order on purpose: the VM's histogram is indexed [x, y].
        self._img_item = pg.ImageItem()
        self._img_item.setLookupTable(pg.colormap.get("plasma").getLookupTable(0.0, 1.0, 256))
        self._img_item.setVisible(False)
        self._vb.addItem(self._img_item)

        self._scatter = pg.ScatterPlotItem(size=4, pen=None, brush=pg.mkBrush(255, 200, 0, 200))
        self._vb.addItem(self._scatter)

        # Analysis-zone bounds: the only region points can appear in after the zone cut.
        self._zone_rings: list[QGraphicsEllipseItem] = []
        for _ in range(2):
            ring = QGraphicsEllipseItem()
            pen = QPen(QColor(120, 200, 255))
            pen.setCosmetic(True)
            pen.setWidth(1)
            ring.setPen(pen)
            self._vb.addItem(ring)
            self._zone_rings.append(ring)

        self.body_layout.addWidget(self._glw, stretch=1)

        config_view = IonConfigView(self.vm, parent=self)
        controls = QHBoxLayout()
        self._mode_switch = SegmentedControl([
            ("Current", IonDisplayMode.CURRENT),
            ("Rolling", IonDisplayMode.ROLLING),
            ("Accumulated", IonDisplayMode.ACCUMULATED),
        ])
        self._mode_switch.set_value(self.vm.mode)
        controls.addWidget(self._mode_switch)
        self._clear_btn = QPushButton("Clear")
        controls.addWidget(self._clear_btn)
        self._stats = QLabel("–")
        controls.addWidget(self._stats, stretch=1)
        self._config_btn = QPushButton("Config")
        controls.addWidget(self._config_btn)
        self.body_layout.addLayout(controls)

        self._mode_switch.value_changed.connect(self.vm.set_mode)
        self._clear_btn.clicked.connect(self.vm.clear)
        self._config_btn.clicked.connect(config_view.open)
        self._connect(self.vm.points_updated, self._on_points)
        self._connect(self.vm.image_updated, self._on_image)
        self._connect(self.vm.stats_updated, self._on_stats)
        self._connect(self.vm.config_updated, self._on_config_updated)

        self._on_config_updated()

    def _on_points(self, x: np.ndarray, y: np.ndarray) -> None:
        self._img_item.setVisible(False)
        self._scatter.setVisible(True)
        self._scatter.setData(x, y)

    def _on_image(self, hist: np.ndarray, extent: Range) -> None:
        self._scatter.setVisible(False)
        self._img_item.setVisible(True)
        self._img_item.setImage(hist, autoLevels=True)
        span = extent.max - extent.min
        self._img_item.setRect(QRectF(extent.min, extent.min, span, span))

    def _on_stats(self, result: IonFrameResult) -> None:
        text = f"{result.n_detected} detected, {len(result.points)} in zone"
        if result.c2t is not None:
            err = "" if math.isnan(result.c2t.error) else f" ± {result.c2t.error:.3f}"
            text += f"   ⟨cos²θ⟩ = {result.c2t.value:.3f}{err}"
        self._stats.setText(text)

    def _on_config_updated(self) -> None:
        zone = self.vm.settings.analysis_zone
        for ring, r in zip(self._zone_rings, (zone.min, zone.max)):
            ring.setRect(QRectF(-r, -r, 2 * r, 2 * r))
        pad = zone.max * 1.05
        self._vb.setRange(xRange=(-pad, pad), yRange=(-pad, pad), padding=0)
