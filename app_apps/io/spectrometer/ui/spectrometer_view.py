from __future__ import annotations

from PySide6.QtWidgets import QWidget

from base_core.quantities.enums import Prefix
from base_qt.ui.form import DirtyForm, DirtyFormWidget, IntSpec, TimeSpec

from app_apps.io.spectrometer.ui.spectrometer_view_model import SpectrometerViewModel

TITLE = "Spectrometer"


class SpectrometerControls(DirtyFormWidget):
    """The settings form itself, free of any window. The Devices PANEL embeds this
    directly and the Devices-MENU popout wraps it, so there is one implementation."""

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

    def __init__(self, vm: SpectrometerViewModel, parent: QWidget | None = None) -> None:
        self._vm = vm
        super().__init__(vm.config, parent)
        # Re-read whenever the config is written back, from here or from the other place
        # this device is shown. Both forms hang off the one shared VM, so this keeps them
        # showing the same numbers instead of one of them holding a stale snapshot.
        vm.config_updated.connect(self.reload)

    def on_apply(self) -> None:
        self._vm.set_config()


class SpectrometerView(DirtyForm):
    """The floating Devices-menu popout, wrapping the same controls the Devices page
    embeds. No ``vm=``: the view model is a singleton shared with that page, so closing
    this popout must hide it rather than tear those subscriptions down."""

    def __init__(self, vm: SpectrometerViewModel, parent: QWidget) -> None:
        self._vm = vm
        super().__init__(TITLE, parent)
        self.add_worker_controls(vm)

    def build_form(self) -> SpectrometerControls:
        return SpectrometerControls(self._vm, self)
