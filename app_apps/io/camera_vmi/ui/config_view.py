from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtWidgets import QComboBox

from base_core.quantities.enums import Prefix
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.ui.form import DirtyForm, DirtyFormWidget, FloatSpec, IntSpec, TimeSpec
from base_qt.ui.form.specs import FieldSpec
from camera.base.buffer import PIXEL_FORMAT_DTYPES

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget
    from app_apps.io.camera_vmi.ui.camera_vmi_controls_view_model import CameraVmiControlsViewModel
    from app_apps.io.camera_vmi.ui.camera_vmi_view_model import CameraVmiViewModel


#: Only a stopped worker re-opens the camera on its next start; a paused one resumes on the
#: device as it was opened, so setup-only fields would silently not take effect.
_EDITABLE_STATES = (WorkerStatus.NEW,)


class _ChoiceSpec(FieldSpec):
    """A dropdown over fixed strings. EnumSpec needs an Enum, and pixel_format is a str."""

    def __init__(self, label: str, choices: list[str]) -> None:
        super().__init__(label)
        self._choices = choices

    def create_widget(self) -> QComboBox:
        combo = QComboBox()
        combo.addItems(self._choices)
        return combo

    def set_value(self, widget: QComboBox, value: Any) -> None:  # type: ignore[override]
        widget.setCurrentText(value)

    def get_value(self, widget: QComboBox) -> str:  # type: ignore[override]
        return widget.currentText()

    def connect_change(self, widget: QComboBox, slot: Callable[[], None]) -> None:  # type: ignore[override]
        widget.currentIndexChanged.connect(lambda _: slot())


class CameraConfigForm(DirtyFormWidget):
    """Exposure and gain, the settings that apply live. Used under the docked image.

    Edits the VM's CameraConfig in place; Apply hands it back to the VM to push.

    width/height/pixel_format are deliberately absent: they fix the shared-memory frame
    shape, which cannot change once the buffer exists (see CameraConfig).
    """

    _specs = {
        "exposure_time": TimeSpec("Exposure", Prefix.MILLI, [Prefix.MICRO, Prefix.MILLI],
                                  min=0.01, max=1_000_000, decimals=0, step=100),
        "gain":          FloatSpec("Gain (dB)", 0.0, 48.0, decimals=1, step=0.5),
    }

    def __init__(
        self,
        vm: CameraVmiViewModel | CameraVmiControlsViewModel,
        parent: QWidget | None = None,
    ) -> None:
        # Set before super().__init__ builds and populates the fields.
        self._vm = vm
        super().__init__(vm.config, parent)
        # The config is the single truth: re-read it whenever anyone applies, here, on the
        # Devices page or in the camera panel's Config popout.
        vm.config_updated.connect(self.reload)

    def on_apply(self) -> None:
        self._vm.apply_config()


class CameraSetupForm(CameraConfigForm):
    """The whole CameraConfig, for the Devices-menu popout and the Devices page.

    Exposure and gain stay editable while running, as in CameraConfigForm. Everything
    else is only read when the camera is opened (CameraWorker._start), so it is locked
    unless the worker is stopped -- set it, Apply, then Start.

    Width, height and pixel format also size the shared-memory buffer. Changing them is
    fine; the next Start notices and restarts the camera subprocess with a new buffer
    (CameraWorkerHandle.start), which costs a few seconds once.
    """

    _specs = {
        **CameraConfigForm._specs,
        "device_index": IntSpec("Device index", 0, 16),
        "pixel_format": _ChoiceSpec("Pixel format", list(PIXEL_FORMAT_DTYPES)),
        "width":        IntSpec("Width (px)", 1, 10_000, step=4),
        "height":       IntSpec("Height (px)", 1, 10_000, step=2),
        "offset_x":     IntSpec("Offset x (px)", 0, 10_000, step=2),
        "offset_y":     IntSpec("Offset y (px)", 0, 10_000, step=2),
        "timeout_ms":   IntSpec("Frame timeout (ms)", 1, 600_000, step=100),
    }
    _groups = [
        ("Acquisition", ["exposure_time", "gain"]),
        ("Device", ["device_index", "pixel_format", "timeout_ms"]),
        ("Region of interest", ["width", "height", "offset_x", "offset_y"]),
    ]
    _readonly_when_running = frozenset({
        "device_index", "pixel_format", "timeout_ms", "width", "height", "offset_x", "offset_y",
    })

    def __init__(self, vm: CameraVmiControlsViewModel, parent: QWidget | None = None) -> None:
        super().__init__(vm, parent)
        self.set_running(vm.worker_status not in _EDITABLE_STATES)
        vm.worker_state_changed.connect(
            lambda status: self.set_running(status not in _EDITABLE_STATES)
        )


class CameraVmiDeviceView(DirtyForm):
    """Start/stop and settings for the VMI camera, from the Devices menu. No image: the
    live image is the docked CameraVmiView, which carries its own copy of these fields.

    No ``vm=``: the view model is a singleton shared with the Devices page, so closing
    this popout must hide it rather than tear those subscriptions down -- the same
    arrangement as OscilloscopeView.
    """

    def __init__(self, vm: CameraVmiControlsViewModel, parent: QWidget | None) -> None:
        self._vm = vm
        super().__init__("VMI Camera", parent)
        self.add_worker_controls(vm)

    def build_form(self) -> CameraSetupForm:
        return CameraSetupForm(self._vm, self)
