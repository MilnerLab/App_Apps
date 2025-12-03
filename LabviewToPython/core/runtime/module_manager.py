# core/module_manager.py
from __future__ import annotations
from typing import List
from LabviewToPython.core.interfaces.i_module import IDockModule, IMenuModule, IModule
from LabviewToPython.core.types.host import AppContext, DockSpec, HostServices
from LabviewToPython.modules import discover_modules

class ModuleManager:
    def __init__(self) -> None:
        self._mods: List[IModule] = []

    def load_all(self) -> None:
        self._mods = discover_modules()

    def start_all(self, ctx: AppContext) -> None:
        for m in self._mods:
            m.start(ctx)

    def create_all_docks(self, host: HostServices) -> List[DockSpec]:
        created: List[DockSpec] = []
        for m in self._mods:
            if isinstance(m, IDockModule):
                for spec in m.create_docks():
                    host.add_dock(spec)
                    created.append(spec)
        return created

    def install_all_menus(self) -> None:
        for m in self._mods:
            if isinstance(m, IMenuModule):
                m.install_menus()

    def stop_all(self) -> None:
        for m in self._mods:
            try: m.stop()
            except Exception: pass

    def modules(self) -> List[IModule]:
        return list(self._mods)
