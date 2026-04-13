from __future__ import annotations

from base_core.framework.events import EventBus
from base_qt.app.interfaces import IUiDispatcher
from base_qt.view_models.menu_view_model_base import MenuViewModelBase
from base_qt.views.registry.interfaces import IViewRegistry


class MenuBarVM(MenuViewModelBase):
    def __init__(
        self,
        ui: IUiDispatcher,
        bus: EventBus,
        registry: IViewRegistry,
    ) -> None:
        super().__init__(ui=ui, bus=bus, registry=registry)