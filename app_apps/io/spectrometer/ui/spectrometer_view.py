from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QHBoxLayout, QWidget

from base_core.quantities.enums import Prefix
from base_qt.ui.form import DirtyFormWidget, IntSpec, TimeSpec
from base_qt.ui.panel_view import PanelView

from app_apps.io.spectrometer.ui.spectrometer_view_model import SpectrometerViewModel

if TYPE_CHECKING:
    from app_apps.recording.ui.view_model import SpectrumRecordingViewModel

TITLE = "Spectrometer"


class SpectrometerControls(DirtyFormWidget):
    """The settings form plus recording, free of any window. The Devices PANEL embeds this
    directly and the Devices-MENU popout wraps it, so there is one implementation.

    Unlike other forms, this one places its own Apply: centered under the settings, above
    the Record box, so Apply visibly belongs to the settings and not to recording. Hosts
    therefore must not put ``apply_button`` in their footer.
    """

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

    def __init__(
        self,
        vm: SpectrometerViewModel,
        recording_vm: "SpectrumRecordingViewModel",
        parent: QWidget | None = None,
    ) -> None:
        self._vm = vm
        super().__init__(vm.config, parent)
        # Re-read whenever the config is written back, from here or from the other place
        # this device is shown. Both forms hang off the one shared VM, so this keeps them
        # showing the same numbers instead of one of them holding a stale snapshot.
        vm.config_updated.connect(self.reload)

        apply_row = QHBoxLayout()
        apply_row.addStretch(1)
        apply_row.addWidget(self.apply_button)
        apply_row.addStretch(1)
        self.form_layout.addLayout(apply_row)

        from app_apps.recording.ui.view import SpectrumRecordingControls
        self.form_layout.addWidget(SpectrumRecordingControls(recording_vm, self))

    def on_apply(self) -> None:
        self._vm.set_config()


class SpectrometerView(PanelView):
    """The floating Devices-menu popout, wrapping the same controls the Devices page
    embeds. No ``vm=``: the view models are singletons shared with that page, so closing
    this popout must hide it rather than tear those subscriptions down.

    A plain ``PanelView`` rather than ``DirtyForm``: ``DirtyForm`` pins Apply in the
    footer, and ``SpectrometerControls`` places its own.
    """

    def __init__(
        self,
        vm: SpectrometerViewModel,
        recording_vm: "SpectrumRecordingViewModel",
        parent: QWidget,
    ) -> None:
        self._vm = vm
        super().__init__(TITLE, parent)
        self.add_worker_controls(vm)
        self.form = SpectrometerControls(vm, recording_vm, self)
        self.body_layout.addWidget(self.form)
