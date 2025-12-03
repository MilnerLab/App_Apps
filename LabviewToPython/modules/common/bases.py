from __future__ import annotations
from typing import Optional
from LabviewToPython.core.interfaces.i_eventbus import IEventBus
from LabviewToPython.core.types.host import AppContext, HostServices
from LabviewToPython.core.interfaces.i_module import IModule, IDockModule, IMenuModule

class ModuleBase(IModule):
    def __init__(self) -> None:
        self._bus: Optional[IEventBus] = None
        self._host: Optional[HostServices] = None

    # >>> Non-optional Properties (mit Runtime-Guard)
    @property
    def bus(self) -> IEventBus:
        assert self._bus is not None, "ModuleBase.bus accessed before start(ctx)"
        return self._bus

    @property
    def host(self) -> HostServices:
        assert self._host is not None, "ModuleBase.host accessed before start(ctx)"
        return self._host

    def start(self, ctx: AppContext) -> None:
        self._bus = ctx.bus
        self._host = ctx.host

    def stop(self) -> None:
        pass

class DockModuleBase(ModuleBase, IDockModule):
    pass

class MenuModuleBase(ModuleBase, IMenuModule):
    pass
