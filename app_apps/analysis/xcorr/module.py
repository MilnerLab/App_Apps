"""Registers the XCORR display panel (spec R12–R15).

Pure UI + analysis: the panel reconstructs everything from the routine's bus events
and the ps-native fit in :mod:`app_apps.analysis.xcorr.frequency`, so this module owns
no hardware, spawns no subprocess and has no ``on_startup`` work. It is intentionally
**not** in the headless runner's module list — nothing here is needed to drive a scan,
only to watch one. The view/VM are registered as factories (a fresh pair per dock open),
matching every other panel; wiring the dock lives in ``AppPanelWindow``.
"""
from __future__ import annotations

from base_core.framework.app.context import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule


class AnalysisXcorrModule(BaseModule):
    name = "analysis_xcorr"

    def register(self, c: Container, ctx: AppContext) -> None:
        from base_qt.app.dispatcher import QtDispatcher
        from app_apps.analysis.xcorr.ui.xcorr_display_view import XcorrDisplayView
        from app_apps.analysis.xcorr.ui.xcorr_display_view_model import XcorrDisplayViewModel

        c.register_factory(XcorrDisplayViewModel, lambda c: XcorrDisplayViewModel(
            ctx.event_bus, c.get(QtDispatcher),
        ))
        c.register_factory(XcorrDisplayView, lambda c: XcorrDisplayView(
            c.get(XcorrDisplayViewModel), parent=None,
        ))
