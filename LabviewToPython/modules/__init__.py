from typing import List
from LabviewToPython.core.interfaces.i_module import IModule
from LabviewToPython.modules.registry import MODULES

def discover_modules() -> List[IModule]:
    """Instantiate all modules without args; bus kommt in start(ctx)."""
    return [cls() for cls in MODULES]
