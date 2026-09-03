from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from app_apps.app.config_store import ConfigStore
from app_apps.app.module import AppModule
from app_apps.app.service_config import ServiceConfig
from app_apps.app.shell import AppShell
from app_apps.io.spectrometer.module import SpectrometerModule
from app_apps.io.control_readout.module import ControlReadoutModule
from app_apps.io.oscilloscope.module import OscilloscopeModule
from app_apps.analysis.phase_control.module import PhaseControlModule
from app_apps.analysis.xcorr.module import AnalysisXcorrModule
from app_apps.routines.module import RoutinesModule
from base_core.framework.app import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.events import EventBus
from base_core.framework.lifecycle.cleanup_collection import CleanupCollection
from base_core.framework.log import default_log_file, setup_logging
from base_core.framework.modules import ModuleManager
from base_qt.app.dispatcher import QtDispatcher


def build_context() -> AppContext:
    # Configures the *root* logger (see G10 / base_core.framework.log), so every
    # logging.getLogger(__name__) in the codebase reaches the console and the
    # rotating file. Device subprocesses configure their own in BaseSubprocessMain.
    log = setup_logging("phase_control_lab", level=logging.INFO,
                        log_file=default_log_file("app_apps"))
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
    # Registered before the modules bootstrap: every module that owns a configuration
    # object binds it here, so the store has to exist before the first one runs.
    store = ConfigStore.of(c)
    c.register_instance(ServiceConfig, store.build("services", ServiceConfig(
        spectrometer=True,
        rotator=False,
        phase_control=True,
        assistant=False,  # LLM control layer off by default; flip to enable (also toggleable at runtime)
    )))
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
        OscilloscopeModule(),
        PhaseControlModule(),
        AnalysisXcorrModule(),
        RoutinesModule(),
    ]
    mm = ModuleManager(modules)
    mm.bootstrap(c, ctx)
    ctx.lifecycle.add(lambda: mm.shutdown(c, ctx))

    dispatcher = c.get(QtDispatcher)
    shell = AppShell(c, ctx.event_bus, dispatcher)
    shell.show()

    rc = app.exec()
    # Before the lifecycle tears the modules down: shutdown may close handles that the
    # configuration objects are read through, and this is the last moment they are all
    # certainly intact. The shell also autosaves while running, so a crash costs at most
    # the last few seconds of edits.
    c.get(ConfigStore).save()
    ctx.lifecycle.clear()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
