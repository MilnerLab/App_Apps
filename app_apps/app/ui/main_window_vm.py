from __future__ import annotations

from base_core.framework.events import EventBus
from base_qt.app.interfaces import IUiDispatcher
from base_qt.view_models.main_window_view_model_base import MainWindowViewModelBase


class MainWindowVM(MainWindowViewModelBase):
    def __init__(self, ui: IUiDispatcher, bus: EventBus) -> None:
        super().__init__(ui=ui, bus=bus)