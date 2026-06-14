"""Author-written linear routines.

Each script module defines `@routine`-decorated functions; importing the module runs the
decorator and registers the routine. Add new scripts here and import them below so they
self-register when `LinearRoutinesModule` loads.

    from app_apps.routines.linear.scripts import delay_freq_sweep  # noqa: F401

Every non-underscore module in this package is auto-imported on package load, so dropping a new
script here (by hand or via the assistant's accepted planner code) makes its `@routine`
functions self-register — no edit to this file needed.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil

log = logging.getLogger(__name__)

for _module in pkgutil.iter_modules(__path__):
    if _module.name.startswith("_"):
        continue
    try:
        importlib.import_module(f"{__name__}.{_module.name}")
    except Exception:  # a broken script must not take down the whole app
        log.exception("failed to import routine script %r", _module.name)
