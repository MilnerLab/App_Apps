# your_app/modules/shell/module.py
from __future__ import annotations

import numpy as np

from app_apps.io.spectrometer.domain import ISpectrometerTaskRunner
from app_apps.io.spectrometer.shared_spectrum_buffer import SharedSpectrumBuffer
from base_core.framework.app import AppContext
from base_core.framework.app.context import ThreadPoolExecutor
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule
from base_core.framework.subprocess.json_endpoint import JsonlSubprocessEndpoint
from spm_002.config import PYTHON32_PATH



class SpectrometerModule(BaseModule):
   
    name = "spectrometer"
    requires = ()
    
    buffer = SharedSpectrumBuffer.create(
        name="spectrometer_buffer",
        slot_count=8,
        pixel_count=3648,
        dtype=np.float64,
    )
    
    '''
metadata_json = buffer.spec.to_json()
# subprocess
spec = SharedRingBufferSpec.from_json(metadata_json)
buffer = SharedSpectrumBuffer.attach(spec)

# first write
buffer.write_spectrum(
    slot=slot,
    wavelengths=wavelengths,
    intensities=intensities,
    frame_id=frame_id,
    timestamp_ns=timestamp_ns,
)

# later writes
buffer.write_spectrum(
    slot=slot,
    intensities=intensities,
    frame_id=frame_id,
    timestamp_ns=timestamp_ns,
)'''

    def register(self, c: Container, ctx: AppContext) -> None:
        
        c.register_instance(SharedSpectrumBuffer, self.buffer)
        
        io_spectrometer_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spectrometer")
        c.register_singleton(ISpectrometerTaskRunner, lambda c: TaskRunner(io_spectrometer_exec))
        
        c.register_singleton(JsonlSubprocessEndpoint, lambda c: JsonlSubprocessEndpoint(argv=[PYTHON32_PATH, "-u", "-m", "spm_002.spectrometer_server"],))
        
      
