"""DI module wiring the assistant into the app (off by default).

Builds an `Assistant` over the existing `LinearRoutineRunner` with a `ClaudeClient`, enabled
per `ServiceConfig.assistant` (default False). When disabled, `handle()` short-circuits before
any LLM call, so no API key / `anthropic` package is needed unless it's turned on. The
runtime kill switch (`enable()`/`disable()`) lets a UI toggle it live.

Wire-in: add `AssistantModule()` to `app.py`'s `modules=[...]`.
"""
from __future__ import annotations

from app_apps.app.service_config import ServiceConfig
from app_apps.assistant.assistant import Assistant
from app_apps.assistant.client import ClaudeClient
from app_apps.assistant.prompt import build_system_prompt
from app_apps.routines.linear.module import LinearRoutinesModule
from app_apps.routines.linear.runner import LinearRoutineRunner
from base_core.framework.app.context import AppContext
from base_core.framework.di import Container
from base_core.framework.modules import BaseModule


class AssistantModule(BaseModule):
    name = "assistant"
    requires = (LinearRoutinesModule,)  # needs the routine runner

    def register(self, c: Container, ctx: AppContext) -> None:
        config = c.try_get(ServiceConfig)
        enabled = bool(config.assistant) if config is not None else False
        assistant = Assistant(
            bus=ctx.event_bus,
            runner=c.get(LinearRoutineRunner),
            client=ClaudeClient(),
            system_prompt=build_system_prompt(),
            enabled=enabled,
        )
        c.register_instance(Assistant, assistant)

    def on_shutdown(self, c: Container, ctx: AppContext) -> None:
        assistant = c.try_get(Assistant)
        if assistant is not None:
            assistant.disable()
