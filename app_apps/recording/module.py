from __future__ import annotations

from base_core.framework.app.context import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule

from app_apps.analysis.phase_control.module import PhaseControlModule
from app_apps.analysis.phase_control.phase_stabilization_handle import PhaseStabilizationHandle
from app_apps.io.control_readout.mfa_cc.handler import MfaccHandle
from app_apps.io.control_readout.module import ControlReadoutModule
from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle
from app_apps.io.spectrometer.module import SpectrometerModule
from app_apps.io.spectrometer.spectrometer_worker_handler import SpectrometerWorkerHandle
from app_apps.recording.spectrum_recorder import SpectrumRecorder
from app_apps.recording.spectrum_recording_service import SpectrumRecordingService


class RecordingModule(BaseModule):
    """Standalone data recording: for now the spectrometer stream into its own HDF5 file."""

    name = "recording"
    requires = (SpectrometerModule, ControlReadoutModule, PhaseControlModule)

    def register(self, c: Container, ctx: AppContext) -> None:
        # A factory, not an instance: a recorder records one run, and a routine embedding
        # the spectra in its own file needs a recorder of its own.
        c.register_factory(SpectrumRecorder, lambda c: SpectrumRecorder(
            ctx.event_bus,
            spectrometer=c.get(SpectrometerWorkerHandle),
            stabilization=c.get(PhaseStabilizationHandle),
            grating=c.get(Uts150ccHandle),
            delay=c.get(MfaccHandle),
        ))
        c.register_singleton(SpectrumRecordingService, lambda c: SpectrumRecordingService(
            ctx.event_bus, make_recorder=lambda: c.get(SpectrumRecorder)
        ))

        from base_qt.app.dispatcher import QtDispatcher
        from app_apps.recording.ui.view_model import SpectrumRecordingViewModel

        # Singleton, like the device VMs: the Record box appears both in the spectrometer
        # popout and on the Devices page, and both must show the one recording.
        c.register_singleton(SpectrumRecordingViewModel, lambda c: SpectrumRecordingViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(SpectrumRecordingService)
        ))

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        # Close the file cleanly before the spectrometer goes away under the recorder.
        c.get(SpectrumRecordingService).stop()
