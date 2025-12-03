from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List
from LabviewToPython.core.types.host import DockSpec, HostServices, AppContext

class IModule(ABC):
    def start(self, ctx: AppContext) -> None: ...
    def stop(self) -> None: ...

class IDockModule(IModule):
    @abstractmethod
    def create_docks(self) -> List[DockSpec]: ...

class IMenuModule(IModule):
    @abstractmethod
    def install_menus(self) -> None: ...
