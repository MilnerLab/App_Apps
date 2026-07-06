from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from base_core.quantities.enums import Prefix
from base_qt.ui.form import ConfigForm, FrequencySpec, TimeSpec

from app_apps.routines.cfg_calibration.ui.view_model import CfgCalibrationViewModel


class CfgCalibrationView(ConfigForm):
    _specs = {
        "fwhm": TimeSpec("FWHM", Prefix.PICO),
        "min":  FrequencySpec("Min frequency", Prefix.MEGA, min=-1e9, max=1e9),
        "max":  FrequencySpec("Max frequency", Prefix.MEGA, min=-1e9, max=1e9),
    }

    def __init__(self, vm: CfgCalibrationViewModel, parent: QWidget) -> None:
        self._vm = vm
        super().__init__("CFG Calibration", vm.cfg_range, parent, vm=vm)

        start_row = QHBoxLayout()
        start_row.addStretch(1)
        start_btn = QPushButton("Start Step 1")
        start_btn.clicked.connect(vm.start)
        start_row.addWidget(start_btn)
        self.body_layout.addLayout(start_row)

    def on_apply(self) -> None:
        pass
