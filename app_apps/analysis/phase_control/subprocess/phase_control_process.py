from __future__ import annotations

from base_core.ipc.subprocess_main import BaseSubprocessMain
from app_apps.analysis.phase_control.subprocess.domain.envelope_config import EnvelopeConfig
from app_apps.analysis.phase_control.subprocess.domain.phase_stabilization_config import StabilizationConfig, FringeFitParams
from app_apps.analysis.phase_control.subprocess.envelope_worker import EnvelopeWorker
from app_apps.analysis.phase_control.subprocess.phase_stabilization_worker import PhaseStabilizationWorker
from spm_002.buffer import SpectrumBuffer


class PhaseControlProcess(BaseSubprocessMain):
    def setup(self) -> None:
        self.register_buffer_class(SpectrumBuffer)
        get_buffer = lambda: self.get_buffer(SpectrumBuffer)  # noqa: E731
        self.register_worker(PhaseStabilizationWorker(
            bus=self.bus,
            connector=self.connector,
            config=StabilizationConfig(params=FringeFitParams()),
            get_buffer=get_buffer,
        ))
        self.register_worker(EnvelopeWorker(
            bus=self.bus,
            connector=self.connector,
            config=EnvelopeConfig(),
            get_buffer=get_buffer,
        ))


if __name__ == "__main__":
    PhaseControlProcess.main()
