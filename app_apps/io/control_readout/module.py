from __future__ import annotations

import logging

from app_apps.io.control_readout.ell14.handler import ELL14RotatorHandle
from app_apps.io.control_readout.fms300pp.handler import Fms300ppHandle
from app_apps.io.control_readout.mfa_cc.handler import MfaccHandle
from app_apps.io.control_readout.picomotor.handler import PicomotorHandle
from app_apps.io.control_readout.rgv.handler import RgvHandle
from app_apps.io.control_readout.servo.handler import ServoShutterHandle
from app_apps.io.control_readout.service import ControlReadoutService
from app_apps.io.control_readout.uts150cc.handler import Uts150ccHandle
from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule

log = logging.getLogger(__name__)

_HANDLE_TYPES = (
    ELL14RotatorHandle,
    Fms300ppHandle,
    MfaccHandle,
    Uts150ccHandle,
    RgvHandle,
    PicomotorHandle,
    ServoShutterHandle,
)


class ControlReadoutModule(BaseModule):
    name = "control_readout"

    def register(self, c: Container, ctx: AppContext) -> None:
        service = ControlReadoutService(bus=ctx.event_bus)

        for handle_type in _HANDLE_TYPES:
            handle = handle_type(bus=ctx.event_bus)
            service.add_handle(handle)
            c.register_instance(handle_type, handle)

        c.register_instance(ControlReadoutService, service)

        from app_apps.io.control_readout.ell14.ui.view_model import ELL14RotatorViewModel
        from app_apps.io.control_readout.ell14.ui.view import ELL14RotatorView
        from app_apps.io.control_readout.fms300pp.ui.view_model import Fms300ppViewModel
        from app_apps.io.control_readout.fms300pp.ui.view import Fms300ppView
        from app_apps.io.control_readout.mfa_cc.ui.view_model import MfaccViewModel
        from app_apps.io.control_readout.mfa_cc.ui.view import MfaccView
        from app_apps.io.control_readout.picomotor.ui.view_model import PicomotorViewModel
        from app_apps.io.control_readout.picomotor.ui.view import PicomotorView
        from app_apps.io.control_readout.rgv.ui.view_model import RgvViewModel
        from app_apps.io.control_readout.rgv.ui.view import RgvView
        from app_apps.io.control_readout.uts150cc.ui.view_model import Uts150ccViewModel
        from app_apps.io.control_readout.uts150cc.ui.view import Uts150ccView
        from base_qt.app.dispatcher import QtDispatcher

        c.register_singleton(ELL14RotatorViewModel, lambda c: ELL14RotatorViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(ELL14RotatorHandle)
        ))
        c.register_singleton(Fms300ppViewModel, lambda c: Fms300ppViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(Fms300ppHandle)
        ))
        c.register_singleton(MfaccViewModel, lambda c: MfaccViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(MfaccHandle)
        ))
        c.register_singleton(Uts150ccViewModel, lambda c: Uts150ccViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(Uts150ccHandle)
        ))
        # PhaseControlService, not PhaseStabilizationHandle: the envelope worker drives the
        # same half-wave plate, and stop_worker() stops whichever of the two is active.
        # Resolved lazily inside the factory, so module registration order does not matter.
        from app_apps.analysis.phase_control.service import PhaseControlService
        c.register_singleton(RgvViewModel, lambda c: RgvViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(RgvHandle),
            c.get(PhaseControlService),
        ))
        c.register_singleton(PicomotorViewModel, lambda c: PicomotorViewModel(
            ctx.event_bus, c.get(QtDispatcher), c.get(PicomotorHandle)
        ))

        c.register_singleton(ELL14RotatorView, lambda c: ELL14RotatorView(c.get(ELL14RotatorViewModel), parent=None))
        c.register_singleton(Fms300ppView, lambda c: Fms300ppView(c.get(Fms300ppViewModel), parent=None))
        c.register_singleton(MfaccView, lambda c: MfaccView(c.get(MfaccViewModel), parent=None))
        c.register_singleton(Uts150ccView, lambda c: Uts150ccView(c.get(Uts150ccViewModel), parent=None))
        c.register_singleton(RgvView, lambda c: RgvView(c.get(RgvViewModel), parent=None))
        c.register_singleton(PicomotorView, lambda c: PicomotorView(c.get(PicomotorViewModel), parent=None))

    def on_startup(self, c: Container, ctx: AppContext) -> None:
        c.get(ControlReadoutService).start()
        if ctx.status == AppStatus.CONNECTED:
            
            for handle_type in _HANDLE_TYPES:
                c.get(handle_type).start()

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        service = c.get(ControlReadoutService)
        # stop() MUST always run — otherwise the subprocess is orphaned still holding
        # COM2. So the graceful steps (pause + the COM2 release handshake) go in a try
        # whose finally is stop(); a failure in any of them (e.g. a dead connector
        # raising BrokenPipeError from pause()) must not skip the terminate (G25-class
        # orphan). Each pause() is guarded individually so one bad handle can't stop
        # the others or skip the release.
        try:
            for handle_type in _HANDLE_TYPES:
                try:
                    c.get(handle_type).pause()
                except Exception:
                    log.exception("control_readout shutdown: pause(%s) failed",
                                  handle_type.__name__)
            # Close the serial ports (COM2) gracefully in the subprocess BEFORE stop()
            # hard-kills it — otherwise the ESP301's USB bridge is left to an abrupt
            # close and can wedge (defect G19).
            service.release_hardware()
        finally:
            service.stop()
