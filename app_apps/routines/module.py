from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from base_core.framework.app.context import AppContext
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer

from app_apps.io.spectrometer.module import SpectrometerModule
from app_apps.routines.centrifuge_calibration.routine import CentrifugeCalibrationRoutine


class CentrifugeCalibrationModule(BaseModule):
    name = "centrifuge_calibration"
    requires = (SpectrometerModule,)

    def register(self, c: Container, ctx: AppContext) -> None:
        c.register_singleton(CentrifugeCalibrationRoutine, lambda c: CentrifugeCalibrationRoutine(
            bus=ctx.event_bus,
            io=TaskRunner(
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="centrifuge-cal-io")
            ),
            spectrum_buffer=c.get(SharedSpectrumBuffer),
        ))

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        routine = c.get(CentrifugeCalibrationRoutine)
        # Routines are not auto-started — the VM starts them on user action.
        # Register stop so an in-progress routine is aborted cleanly on shutdown.
        ctx.lifecycle.add(routine.stop)
