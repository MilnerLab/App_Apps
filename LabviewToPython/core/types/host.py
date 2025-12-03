from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Iterable
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QMainWindow, QMenuBar, QDockWidget
from LabviewToPython.core.events.eventbus import IEventBus  # Interface okay hier

@dataclass
class DockSpec:
    object_name: str
    title: str
    factory: Callable[[QWidget], QWidget]
    area: Qt.DockWidgetArea = Qt.DockWidgetArea.LeftDockWidgetArea

@dataclass
class HostServices:
    window: QMainWindow
    menubar: QMenuBar
    add_dock: Callable[[DockSpec], QDockWidget]
    get_all_docks: Callable[[], Iterable[QDockWidget]]

@dataclass
class AppContext:
    bus: IEventBus
    host: HostServices
