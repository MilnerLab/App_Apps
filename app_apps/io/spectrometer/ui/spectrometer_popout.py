from __future__ import annotations

from PySide6.QtWidgets import QWidget

from base_core.quantities.enums import Prefix
from base_qt.ui.form import IntSpec, TimeSpec
from base_qt.ui.form.config_form import ConfigForm

from base_qt.ui.worker_control_widget import WorkerControlWidget

from app_apps.io.spectrometer.ui.spectrometer_vm import SpectrometerVM


class SpectrometerPopout(ConfigForm):
    _specs = {
        "exposure_time":      TimeSpec("Exposure", Prefix.MILLI),
        "average":          IntSpec("Averages", 1, 100),
        "device_index":     IntSpec("Device index", 0, 9),
        "dark_subtraction": IntSpec("Dark subtraction", 0, 1),
        "mode":             IntSpec("Mode", 0, 5),
        "scan_delay":       IntSpec("Scan delay", 0, 9_999),
    }
    _groups = [
        ("Acquisition", ["exposure_time", "average"]),
        ("Hardware",    ["device_index", "dark_subtraction", "mode", "scan_delay"]),
    ]

    def __init__(self, vm: SpectrometerVM, parent: QWidget) -> None:
        self._vm = vm
        super().__init__("Spectrometer", vm.config, parent)

        ctrl = WorkerControlWidget(vm.start, vm.pause, vm.reset, parent=self)
        ctrl.set_status(vm.worker_status)
        vm.worker_state_changed.connect(ctrl.set_status)
        self.body_layout.insertWidget(0, ctrl)

    def on_apply(self) -> None:
        self._vm.set_config()
