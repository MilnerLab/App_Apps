import sys
from PySide6.QtWidgets import QApplication
from LabviewToPython.core.runtime.module_manager import ModuleManager
from LabviewToPython.core.events.eventbus import EventBus
from LabviewToPython.views.main_window import MainWindow

def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("YourLab")
    app.setApplicationName("LabApp")

    bus = EventBus()
    modules_manager = ModuleManager()
    modules_manager.load_all()  # discover & instantiate feature modules

    win = MainWindow(modules_manager, bus)   # shell only knows the manager
    win.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
