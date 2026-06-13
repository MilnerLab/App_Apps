"""The runner that turns a registered linear routine into a live, app-integrated task.

`LinearRoutineRunner` is a `base_core` `Routine`, so it plugs into the app lifecycle exactly
like any other routine (UI can observe `is_running`; the lifecycle calls `stop()` on
shutdown). It runs the author's plain function on a background `TaskRunner` thread, injecting
a fresh `Lab` and the launch parameters, and publishes lifecycle events.

Concurrency model: **single-flight** — one routine at a time. `launch()` rejects a second
routine while one is active. Because only one routine runs at once, two routines can never
race the same stage, so no per-stage ownership guard is needed here. (When the M4 control
loops land — which *can* run alongside a routine — per-stage `StageOwnership` integrates at
the `lab` verb level, not here.)
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from app_apps.routines.linear.cancel import CancelToken, RoutineCancelled, RoutineError
from app_apps.routines.linear.events import (
    RoutineCancelledEvent,
    RoutineCompleted,
    RoutineFailed,
    RoutineStarted,
)
from app_apps.routines.linear.lab import Lab
from app_apps.routines.linear.registry import RoutineSpec, get_routine
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from base_core.framework.routines.routine import Routine

log = logging.getLogger(__name__)

#: Builds a fresh Lab for one run, given that run's cancel token and parameters.
LabFactory = Callable[[CancelToken, dict[str, Any]], Lab]


class RoutineBusy(RoutineError):
    """Raised when a routine is launched while another is already running (single-flight)."""


class LinearRoutineRunner(Routine):
    """Runs registered `@routine` functions, one at a time, as background tasks."""

    KEY = "linear_routine"

    _running: bool  # set by base Routine.__init__; annotated here for the type checker

    def __init__(self, bus: EventBus, io: TaskRunner, lab_factory: LabFactory) -> None:
        super().__init__(bus, io)
        self._lab_factory = lab_factory
        self._lock = threading.Lock()
        self._cancel: CancelToken | None = None
        self._active: str | None = None

    @property
    def active_routine(self) -> str | None:
        """Name of the routine currently running, or None."""
        return self._active

    def launch(self, name: str, **params: Any) -> None:
        """Start a registered routine by name with the given parameters (non-blocking).

        Raises RoutineNotFound if `name` isn't registered, or RoutineBusy if another routine
        is already running.
        """
        spec = get_routine(name)  # RoutineNotFound if missing — fail before claiming the slot
        with self._lock:
            if self._running:
                raise RoutineBusy(f"routine {self._active!r} is already running")
            self._running = True
            self._active = name
            self._cancel = CancelToken()
            cancel = self._cancel

        self._bus.publish(RoutineStarted(name=name, params=dict(params)))
        self._io.run(
            lambda: self._execute(spec, params, cancel),
            on_success=lambda _result: self._finish(name, None),
            on_error=lambda error: self._finish(name, error),
            key=self.KEY,
        )

    def _execute(self, spec: RoutineSpec, params: dict[str, Any], cancel: CancelToken) -> None:
        lab = self._lab_factory(cancel, dict(params))
        try:
            spec.func(lab, **params)
        finally:
            lab.close()

    def _finish(self, name: str, error: BaseException | None) -> None:
        with self._lock:
            self._running = False
            self._active = None
            self._cancel = None

        if error is None:
            self._bus.publish(RoutineCompleted(name=name))
        elif isinstance(error, RoutineCancelled):
            self._bus.publish(RoutineCancelledEvent(name=name))
        else:
            log.error("linear routine %s failed: %r", name, error)
            self._bus.publish(RoutineFailed(name=name, error=repr(error)))

    # -- base Routine ABC ---------------------------------------------------------------

    def start(self) -> None:
        """Not used for the multi-routine runner — call `launch(name, **params)` instead."""
        raise RoutineError(
            "LinearRoutineRunner has no nullary start(); use launch(name, **params)"
        )

    def stop(self) -> None:
        """Cooperatively cancel the active routine (idempotent). Safe on shutdown."""
        with self._lock:
            cancel = self._cancel
        if cancel is not None:
            cancel.cancel()
