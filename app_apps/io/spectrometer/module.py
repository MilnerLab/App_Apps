from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np

from app_apps.io.spectrometer.service import SpectrometerService
from base_core.framework.app.app_message import AppMessage, MessageLevel
from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from base_core.framework.subprocess.shared_memory.shared_memory_base_messages import base_registry
from base_core.framework.subprocess.shared_memory.shared_buffer_coordinator import (
    SharedBufferCoordinator,
)
from base_core.framework.subprocess.worker_protocol import WorkerError
from spm_002.config import PYTHON32_PATH, SpectrometerConfig
from spm_002.messages import SetSpectrometerConfig
from spm_002.shared_spectrum_buffer import SharedSpectrumBuffer


class SpectrometerModule(BaseModule):
    name = "spectrometer"
    requires = ()

    BUFFER_ID = "spectrometer"
    CONSUMER_IDS = ["ui"]

    def register(self, c: Container, ctx: AppContext) -> None:
        buffer = SharedSpectrumBuffer.create(
            name="spectrometer_buffer",
            slot_count=8,
            pixel_count=3648,
            dtype=np.float64,
        )
        coordinator = SharedBufferCoordinator(
            slot_count=buffer.spec.slot_count,
            consumer_bits={"ui": 1 << 0},
        )

        c.register_instance(SharedSpectrumBuffer, buffer)
        c.register_instance(SharedBufferCoordinator, coordinator)
        c.register_singleton(SpectrometerService, lambda c: SpectrometerService(
            io=TaskRunner(
                ThreadPoolExecutor(max_workers=2, thread_name_prefix="spectrometer-io")
            ),
            endpoint=JsonlSubprocessEndpoint(
                argv=[PYTHON32_PATH, "-u", "-m", "spm_002.spectrometer_process"],
                registry=base_registry().extend(SetSpectrometerConfig),
            ),
            bus=ctx.event_bus,
            buffer=c.get(SharedSpectrumBuffer),
            coordinator=c.get(SharedBufferCoordinator),
        ))

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        buffer = c.get(SharedSpectrumBuffer)

        # Buffer cleanup registered first → runs last (lifecycle is reverse order).
        # This ensures the subprocess is stopped before shared memory is released.
        def _cleanup_buffer() -> None:
            buffer.close()
            if buffer.is_owner:
                buffer.unlink()

        ctx.lifecycle.add(_cleanup_buffer)

        if ctx.status == AppStatus.OFFLINE:
            return

        svc = c.get(SpectrometerService)

        def _on_worker_error(msg: WorkerError) -> None:
            if msg.worker_name != "spectrometer":
                return
            ctx.event_bus.publish(AppMessage(
                f"Spectrometer crashed: {msg.error}", MessageLevel.ERROR
            ))

        ctx.lifecycle.add(ctx.event_bus.subscribe(WorkerError, _on_worker_error))
        svc.start()
        ctx.lifecycle.add(svc.stop)
        svc.worker("spectrometer").request_async(
            SetSpectrometerConfig(config=SpectrometerConfig()),
            key="spectrometer.init_config",
        )
