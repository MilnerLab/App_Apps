"""View-model for the picomotor mirror-control panel.

Holds the operator's current step increment and the last known counters; every
number it exposes came from the controller. See :class:`PicomotorHandle` for why it
never accumulates its own total.
"""
from __future__ import annotations

from PySide6.QtCore import Signal

from base_core.framework.events import EventBus
from base_core.ipc.worker_handle import WorkerStatus
from base_qt.app.dispatcher import QtDispatcher
from base_qt.ui.panel_view_model import PanelViewModel, ui_thread
from control_readout.picomotor.config import DEFAULT_MIRRORS, MirrorAxes

from app_apps.io.control_readout.picomotor.events import (
    PicomotorStepsChanged,
    PicomotorWorkerStateChanged,
)
from app_apps.io.control_readout.picomotor.handler import PicomotorHandle

#: Increment presets, and the default. The operator's practice: 50 clearly oversteps
#: but brackets the optimum in a few clicks, then ~10 is the working increment. The
#: presets exist to preserve that coarse-to-fine workflow; the box stays editable
#: because the useful value is a property of the alignment, not of the software.
INCREMENT_PRESETS = (1, 10, 50)
DEFAULT_INCREMENT = 10


class PicomotorViewModel(PanelViewModel):
    worker_state_changed = Signal(object)      # emits WorkerStatus
    #: The step counters changed — the view refreshes every axis readout.
    steps_changed = Signal()

    def __init__(
        self,
        bus: EventBus,
        dispatcher: QtDispatcher,
        handle: PicomotorHandle,
    ) -> None:
        super().__init__(bus, dispatcher)
        self._handle = handle
        self._increment = DEFAULT_INCREMENT
        self._steps: dict[int, int] = {}
        self._sub(PicomotorWorkerStateChanged, self._on_state_changed)
        self._sub(PicomotorStepsChanged, self._on_steps_changed)

    # -- read side ---------------------------------------------------------

    @property
    def worker_status(self) -> WorkerStatus:
        return self._handle.state

    @property
    def mirrors(self) -> tuple[MirrorAxes, ...]:
        return DEFAULT_MIRRORS

    @property
    def increment(self) -> int:
        return self._increment

    def steps_for(self, axis: int) -> int | None:
        """Counter for one axis, or None if it has not been read back yet.

        None is displayed as "—", not as 0: an unknown counter and a counter that
        genuinely reads zero are different, and on an open-loop stage conflating them
        would tell the operator they are referenced when they are not.
        """
        return self._steps.get(axis)

    # -- commands ----------------------------------------------------------

    def set_increment(self, value: int) -> None:
        self._increment = max(1, int(value))

    def nudge(self, axis: int, sign: int) -> None:
        """Step one axis by ±the current increment."""
        self._handle.step(axis, sign * self._increment)

    def step_to(self, axis: int, steps: int) -> None:
        self._handle.step_to(axis, steps)

    def zero(self, axis: int) -> None:
        self._handle.zero(axis)

    def refresh(self) -> None:
        self._handle.refresh()

    # -- bus handlers ------------------------------------------------------

    @ui_thread
    def _on_state_changed(self, _: PicomotorWorkerStateChanged) -> None:
        self.worker_state_changed.emit(self._handle.state)

    @ui_thread
    def _on_steps_changed(self, e: PicomotorStepsChanged) -> None:
        self._steps = dict(e.steps)
        self.steps_changed.emit()

    def start(self) -> None:
        self._handle.start()

    def pause(self) -> None:
        self._handle.pause()

    def resume(self) -> None:
        self._handle.resume()

    def stop(self) -> None:
        self._handle.stop()
