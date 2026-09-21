from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

from base_core.ipc.worker_handle import WorkerStatus
from base_qt.ui.form import DirtyForm, DirtyFormWidget, IntSpec

from app_apps.io.oscilloscope.ui.oscilloscope_view_model import OscilloscopeViewModel

#: Only NEW, unlike the phase-control form. Pausing the scope stops the producer but
#: leaves the instrument open with its record length already written, so a record edited
#: while paused would not reach the hardware until a full stop and start.
_EDITABLE_STATES = (WorkerStatus.NEW,)

TITLE = "Oscilloscope"


class OscilloscopeControls(DirtyFormWidget):
    """Record settings and the reported timebase, free of any window. The Devices PANEL
    embeds this directly and the Devices-MENU popout wraps it, so there is one
    implementation.

    ``sample_rate_hz`` is deliberately not a field here. The real instrument reports its
    own sample interval per acquisition and that is what the time axis uses, so a rate
    knob would be a control that does nothing on hardware and quietly redefines the mock's
    time base. It stays in the config for the mock alone.
    """

    _specs = {
        "n_samples": IntSpec("Record length", 1, 20_000),
        "channels":  IntSpec("Channels", 1, 2),
    }
    _groups = [("Acquisition", ["n_samples", "channels"])]
    # Both define the shared-memory frame shape and are only written to the instrument
    # when the driver is opened, so they are settable only before the worker starts.
    _readonly_when_running = frozenset({"n_samples", "channels"})

    def __init__(self, vm: OscilloscopeViewModel, parent: QWidget | None = None) -> None:
        self._vm = vm
        super().__init__(vm.config, parent)

        # Read-only, because it comes from the instrument rather than from this form: the
        # operator turning the horizontal knob is the only thing that changes it.
        self._timebase = QLabel()
        self.form_layout.addWidget(self._timebase)
        self._show_timebase()
        vm.timebase_changed.connect(self._show_timebase)

        self.set_running(vm.worker_status not in _EDITABLE_STATES)
        vm.worker_state_changed.connect(
            lambda status: self.set_running(status not in _EDITABLE_STATES)
        )
        # Re-read whenever the config is written back, from here or from the other place
        # this device is shown. Both forms hang off the one shared VM, so this keeps them
        # showing the same numbers instead of one of them holding a stale snapshot.
        vm.config_updated.connect(self.reload)

    def _show_timebase(self) -> None:
        dt_s = self._vm.dt_s
        # No "from the instrument": the MOCK badge above already says when it isn't.
        if dt_s > 0:
            self._timebase.setText(f"Sample interval: {dt_s * 1e9:.3g} ns")
        else:
            self._timebase.setText("Sample interval: not reported yet")

    def on_apply(self) -> None:
        self._vm.set_config()


class OscilloscopeView(DirtyForm):
    """Start/stop and record settings. The live trace lives in the XCORR display panel.

    No ``vm=``: the view model is a singleton shared with the Devices page, so closing
    this popout must hide it rather than tear those subscriptions down."""

    def __init__(self, vm: OscilloscopeViewModel, parent: QWidget) -> None:
        self._vm = vm
        super().__init__(TITLE, parent)
        self.add_worker_controls(vm)

    def build_form(self) -> OscilloscopeControls:
        return OscilloscopeControls(self._vm, self)
