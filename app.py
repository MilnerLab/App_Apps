from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from app_apps.app.module import AppModule
from app_apps.app.service_config import ServiceConfig
from app_apps.app.shell import AppShell
from app_apps.io.spectrometer.module import SpectrometerModule
from app_apps.io.control_readout.module import ControlReadoutModule
from app_apps.analysis.phase_control.module import PhaseControlModule
from app_apps.routines.module import RoutinesModule
from base_core.framework.app import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.events import EventBus
from base_core.framework.lifecycle.cleanup_collection import CleanupCollection
from base_core.framework.log import enable_console_logging, setup_logging
from base_core.framework.modules import ModuleManager
from base_qt.app.dispatcher import QtDispatcher


def build_context() -> AppContext:
    # setup_logging only wires the single "phase_control_lab" logger (propagate=False).
    # enable_console_logging wires the ROOT logger so every module logger created via
    # logging.getLogger(__name__) — across app_apps, base_core and control_readout —
    # also prints to this terminal. Without it, none of the pipeline's log.info calls
    # (fit -> correction -> rotate) are ever visible.
    enable_console_logging(logging.INFO)
    log = setup_logging("phase_control_lab", level=logging.INFO)
    lifecycle = CleanupCollection()
    bus = EventBus()
    return AppContext(
        config={},
        status=AppStatus.OFFLINE,
        log=log,
        event_bus=bus,
        lifecycle=lifecycle,
    )


def build_container(ctx: AppContext) -> Container:
    c = Container()
    c.register_instance(AppContext, ctx)
    c.register_instance(ServiceConfig, ServiceConfig(
        spectrometer=True,
        rotator=False,
        phase_control=True,
        assistant=False,  # LLM control layer off by default; flip to enable (also toggleable at runtime)
    ))
    return c


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv

    app = QApplication(argv)

    ctx = build_context()
    c   = build_container(ctx)

    modules = [
        AppModule(),
        SpectrometerModule(),
        ControlReadoutModule(),
        PhaseControlModule(),
        RoutinesModule(),
    ]
    mm = ModuleManager(modules)
    mm.bootstrap(c, ctx)
    ctx.lifecycle.add(lambda: mm.shutdown(c, ctx))

    dispatcher = c.get(QtDispatcher)
    shell = AppShell(c, ctx.event_bus, dispatcher)
    shell.show()

    rc = app.exec()
    ctx.lifecycle.clear()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
