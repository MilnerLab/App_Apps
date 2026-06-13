"""Author-written linear routines.

Each script module defines `@routine`-decorated functions; importing the module runs the
decorator and registers the routine. Add new scripts here and import them below so they
self-register when `LinearRoutinesModule` loads.

    from app_apps.routines.linear.scripts import delay_freq_sweep  # noqa: F401
"""
from app_apps.routines.linear.scripts import probe_scan  # noqa: F401  (self-registers routines)

__all__ = ["probe_scan"]
