"""Unit tests for AssistantModule (L3 wiring) — DI + the ServiceConfig on/off flag.

No network: the ClaudeClient is constructed but never called (assistant disabled, or we don't
call handle()), and anthropic is imported lazily only inside propose().
"""
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.app.service_config import ServiceConfig
from app_apps.assistant.assistant import Assistant
from app_apps.assistant.client import ClaudeClient
from app_apps.assistant.models import ResultKind
from app_apps.assistant.module import AssistantModule
from app_apps.routines.linear.module import LinearRoutinesModule
from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.events import EventBus
from base_core.framework.lifecycle.cleanup_collection import CleanupCollection


def _ctx() -> AppContext:
    return AppContext(
        config={},
        status=AppStatus.OFFLINE,
        log=logging.getLogger("test"),
        event_bus=EventBus(),
        lifecycle=CleanupCollection(),
    )


def _wire(assistant_flag) -> tuple[Container, AppContext]:
    c = Container()
    ctx = _ctx()
    if assistant_flag is not None:
        c.register_instance(ServiceConfig, ServiceConfig(assistant=assistant_flag))
    LinearRoutinesModule().register(c, ctx)
    AssistantModule().register(c, ctx)
    return c, ctx


class TestAssistantModule(unittest.TestCase):
    def test_disabled_by_default_flag(self) -> None:
        c, _ = _wire(assistant_flag=False)
        assistant = c.get(Assistant)
        self.assertIsInstance(assistant, Assistant)
        self.assertFalse(assistant.enabled)
        self.assertIsInstance(assistant._client, ClaudeClient)

    def test_enabled_when_flag_true(self) -> None:
        c, _ = _wire(assistant_flag=True)
        self.assertTrue(c.get(Assistant).enabled)

    def test_disabled_when_no_service_config(self) -> None:
        c, _ = _wire(assistant_flag=None)
        self.assertFalse(c.get(Assistant).enabled)

    def test_disabled_assistant_handles_without_network(self) -> None:
        c, _ = _wire(assistant_flag=False)
        # handle() must short-circuit (no anthropic, no key) while disabled
        result = c.get(Assistant).handle("scan the probe")
        self.assertEqual(result.kind, ResultKind.DISABLED)

    def test_on_shutdown_disables(self) -> None:
        c, ctx = _wire(assistant_flag=True)
        AssistantModule().on_shutdown(c, ctx)
        self.assertFalse(c.get(Assistant).enabled)


if __name__ == "__main__":
    unittest.main()
