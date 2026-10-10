from __future__ import annotations

from typing import TYPE_CHECKING

from base_core.quantities.enums import Prefix
from base_qt.ui.form import DirtyForm, DirtyFormWidget, FloatSpec, TimeSpec

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget
    from app_apps.io.camera_vmi.ui.camera_vmi_view_model import CameraVmiViewModel


class CameraConfigForm(DirtyFormWidget):
    """Edits the VM's CameraConfig in place; Apply hands it back to the VM to push.

    width/height/pixel_format are deliberately absent: they fix the shared-memory frame
    shape, which cannot change once the buffer exists (see CameraConfig).
    """

    _specs = {
        "exposure_time": TimeSpec("Exposure", Prefix.MICRO, [Prefix.MICRO, Prefix.MILLI],
                                  min=10, max=1_000_000, decimals=0, step=100),
        "gain":          FloatSpec("Gain (dB)", 0.0, 48.0, decimals=1, step=0.5),
    }

    def __init__(self, vm: CameraVmiViewModel, parent: QWidget | None = None) -> None:
        # Set before super().__init__ builds and populates the fields.
        self._vm = vm
        super().__init__(vm.config, parent)

    def on_apply(self) -> None:
        self._vm.apply_config()


class CameraConfigView(DirtyForm):
    """The popout wrapper: fields in the scrolling body, Apply pinned in the footer.

    Constructed inline by ``CameraVmiView`` with its longer-lived VM, so it deliberately
    passes no ``vm=`` -- closing hides it rather than destroying it.
    """

    def __init__(self, vm: CameraVmiViewModel, parent: QWidget) -> None:
        self._vm = vm
        super().__init__("VMI Camera Configuration", parent)
        # The config is the single truth: re-read it whenever anyone applies.
        vm.config_updated.connect(self.form.reload)

    def build_form(self) -> CameraConfigForm:
        return CameraConfigForm(self._vm, self)
