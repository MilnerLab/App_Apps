from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
import sys
print("PYTHON:", sys.executable)

from PySide6.QtWidgets import QApplication


# ---- base_qt (Qt adapters) ----
from app_apps.app.module import AppModule
from app_apps.app.ui.main_window_view import MainWindowView
from base_core.framework.app import AppContext
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.di import Container
from base_core.framework.events import EventBus
from base_core.framework.lifecycle.cleanup_collection import CleanupCollection
from base_core.framework.log import setup_logging
from base_core.framework.modules import ModuleManager
from base_qt.app.dispatcher import QtDispatcher
from base_qt.app.interfaces import IUiDispatcher
from base_core.framework.app.enums import AppStatus
from base_qt.ui.apply import install_ui





def build_context() -> AppContext:
    log = setup_logging("your_app", level=logging.INFO)

    lifecycle = CleanupCollection()
    bus = EventBus()
    ctx = AppContext(
        config={
            "app_name": "Your App",
            "app_status": "offline",
            "rotator_port": "COM6",
        },
        status=AppStatus.OFFLINE,
        log=log,
        event_bus=bus,
        lifecycle=lifecycle,
    )

    return ctx


def build_container(ctx: AppContext) -> Container:
    c = Container()

    c.register_instance(AppContext, ctx)
    c.register_singleton(IUiDispatcher, lambda c: QtDispatcher())

    return c


def get_modules():
    # Add feature modules here (hybrid approach: shell + feature modules)
    return [
        AppModule(),
        # AnalysisModule(),
    ]


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv

    app = QApplication(argv)
    install_ui(app)

    ctx = build_context()
    c = build_container(ctx)

    # Bootstrap modules (register + start; stop happens via lifecycle shutdown)
    module_manager = ModuleManager(get_modules())
    module_manager.bootstrap(c, ctx)
    
    ctx.lifecycle.add(lambda: module_manager.shutdown(c, ctx))

    # Resolve and show main window (ShellModule should register AppMainWindowView as factory)
    win = c.get(MainWindowView)
    win.show()

    rc = app.exec()

    # Stop modules + cleanup
    ctx.lifecycle.clear()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
