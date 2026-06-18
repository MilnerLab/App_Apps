"""DI module that makes linear routines live in the app.

Registers a single `LinearRoutineRunner` and builds the `lab_factory` that wires a fresh
`Lab` from whatever device handles/services are present in the container. Device lookups are
**defensive** (`container.try_get`): a device module that isn't composed in simply yields an
un-wired verb (`LabUnavailable` on use) rather than a hard dependency — so the routines layer
never forces a device subprocess to load and degrades gracefully.

Importing this module imports `linear.scripts`, whose `@routine` decorators self-register.

Wire-in: add `LinearRoutinesModule()` to `app.py`'s `modules=[...]`.
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from base_core.framework.app.context import AppContext
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule

import app_apps.routines.linear.scripts  # noqa: F401  — import for @routine self-registration
from app_apps.io.control_readout.esp_worker_handler import EspHandle
from app_apps.io.control_readout.picomotor_worker_handler import PicomotorHandle
from app_apps.io.control_readout.rgv_worker_handler import RgvHandle
from app_apps.io.control_readout.servo_worker_handler import ServoShutterHandle
from app_apps.io.oscilloscope.buffer import ScopeMemorySpec
from app_apps.io.oscilloscope.oscilloscope_worker_handler import OscilloscopeWorkerHandle
from spm_002.buffer import SpectrumMemorySpec
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle
from app_apps.routines.linear.cancel import CancelToken
from app_apps.routines.linear.lab import Lab
from app_apps.routines.linear.runner import LinearRoutineRunner


def _make_lab_factory(
    c: Container, ctx: AppContext
) -> Callable[[CancelToken, dict[str, Any]], Lab]:
    """Return a factory that builds a Lab per run, wiring whatever devices are present."""

    def factory(cancel: CancelToken, params: dict[str, Any]) -> Lab:
        return Lab(
            bus=ctx.event_bus,
            cancel=cancel,
            params=params,
            esp=c.try_get(EspHandle),
            rgv=c.try_get(RgvHandle),
            picomotor=c.try_get(PicomotorHandle),
            servo=c.try_get(ServoShutterHandle),
            scope_handle=c.try_get(OscilloscopeWorkerHandle),
            scope_spec=c.try_get(ScopeMemorySpec),
            spectrum_handle=c.try_get(SpectrometerWorkerHandle),
            spectrum_spec=c.try_get(SpectrumMemorySpec),
            consumer_id=f"linear-routine-{uuid.uuid4().hex[:8]}",
        )

    return factory


class LinearRoutinesModule(BaseModule):
    name = "linear_routines"

    def register(self, c: Container, ctx: AppContext) -> None:
        io = TaskRunner(
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="linear-routine")
        )
        runner = LinearRoutineRunner(
            ctx.event_bus, io, lab_factory=_make_lab_factory(c, ctx)
        )
        c.register_instance(LinearRoutineRunner, runner)

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        # Routines are user/observer-triggered (runner.launch), never auto-started.
        # Register stop so an in-progress routine aborts cleanly on shutdown.
        ctx.lifecycle.add(c.get(LinearRoutineRunner).stop)
