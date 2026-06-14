"""Unit tests for the Assistant orchestrator (L2) — fake LLM client + fake runner, no network."""
import os
import sys
import unittest
from typing import Any, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.assistant.assistant import Assistant
from app_apps.assistant.client import ToolCall
from app_apps.assistant.events import (
    AssistantDisabled,
    AssistantEnabled,
    AssistantError,
    CodeProposed,
    ProposalReady,
    RoutineAutoLaunched,
)
from app_apps.assistant.models import ResultKind
from app_apps.routines.linear.registry import clear_registry, routine
from app_apps.routines.linear.runner import RoutineBusy
from base_core.framework.events.event_bus import EventBus


class FakeClient:
    def __init__(self, calls: list[Optional[ToolCall]]) -> None:
        self._calls = list(calls)
        self.invocations: list[tuple[str, Optional[str]]] = []

    def propose(self, command, tools, system, *, feedback=None) -> Optional[ToolCall]:
        self.invocations.append((command, feedback))
        return self._calls.pop(0) if self._calls else None


class FakeRunner:
    def __init__(self, busy: bool = False) -> None:
        self.launches: list[tuple[str, dict[str, Any]]] = []
        self._busy = busy
        self.active_routine: Optional[str] = "current" if busy else None
        self.stopped = False

    @property
    def is_running(self) -> bool:
        return self._busy

    def launch(self, name: str, **params: Any) -> None:
        if self._busy:
            raise RoutineBusy("busy")
        self.launches.append((name, params))

    def stop(self) -> None:
        self.stopped = True


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        clear_registry()

        @routine("read_x", safe=True)
        def read_x(lab):
            """Read a safe diagnostic."""

        @routine("move_x", bounds={"x": (0.0, 10.0)})
        def move_x(lab, x: float):
            """Move probe to x."""

        self.bus = EventBus()
        self.runner = FakeRunner()
        self.events: dict[type, list] = {
            t: [] for t in (AssistantEnabled, AssistantDisabled, AssistantError,
                            ProposalReady, RoutineAutoLaunched, CodeProposed)
        }
        for t, lst in self.events.items():
            self.bus.subscribe(t, lst.append)
        self.addCleanup(clear_registry)

    def _assistant(self, calls, *, enabled=True, runner=None) -> Assistant:
        return Assistant(
            bus=self.bus,
            runner=runner or self.runner,
            client=FakeClient(calls),
            enabled=enabled,
        )


class TestKillSwitch(_Base):
    def test_disabled_by_default_makes_no_llm_call(self) -> None:
        client = FakeClient([ToolCall("read_x")])
        a = Assistant(bus=self.bus, runner=self.runner, client=client)  # enabled defaults False
        result = a.handle("read x")
        self.assertEqual(result.kind, ResultKind.DISABLED)
        self.assertEqual(client.invocations, [])  # never called the model
        self.assertEqual(self.runner.launches, [])

    def test_enable_disable_publish_events(self) -> None:
        a = self._assistant([], enabled=False)
        a.enable()
        a.disable()
        self.assertEqual(len(self.events[AssistantEnabled]), 1)
        self.assertEqual(len(self.events[AssistantDisabled]), 1)


class TestDispatch(_Base):
    def test_safe_routine_auto_runs(self) -> None:
        a = self._assistant([ToolCall("read_x")])
        result = a.handle("read the diagnostic")
        self.assertEqual(result.kind, ResultKind.LAUNCHED)
        self.assertEqual(self.runner.launches, [("read_x", {})])
        self.assertEqual(len(self.events[RoutineAutoLaunched]), 1)

    def test_unsafe_routine_requires_confirmation(self) -> None:
        a = self._assistant([ToolCall("move_x", {"x": 5})])
        result = a.handle("move probe to 5")
        self.assertEqual(result.kind, ResultKind.PROPOSAL)
        self.assertEqual(self.runner.launches, [])  # not launched
        self.assertEqual(len(self.events[ProposalReady]), 1)

        confirmed = a.confirm(result.proposal.id)
        self.assertEqual(confirmed.kind, ResultKind.LAUNCHED)
        self.assertEqual(self.runner.launches, [("move_x", {"x": 5.0})])  # coerced to float

    def test_validation_retry_succeeds(self) -> None:
        a = self._assistant([ToolCall("move_x", {"x": 50}), ToolCall("move_x", {"x": 5})])
        result = a.handle("move probe")
        self.assertEqual(result.kind, ResultKind.PROPOSAL)  # 2nd (in-bounds) call accepted
        self.assertEqual(result.proposal.params, {"x": 5.0})

    def test_validation_retry_gives_up(self) -> None:
        a = self._assistant([ToolCall("move_x", {"x": 50}), ToolCall("move_x", {"x": 99})])
        result = a.handle("move probe")
        self.assertEqual(result.kind, ResultKind.ERROR)
        self.assertEqual(len(self.events[AssistantError]), 1)
        self.assertEqual(self.runner.launches, [])

    def test_unknown_routine_errors(self) -> None:
        a = self._assistant([ToolCall("nonexistent")])
        result = a.handle("do something weird")
        self.assertEqual(result.kind, ResultKind.ERROR)

    def test_no_action_when_model_picks_nothing(self) -> None:
        a = self._assistant([None])
        self.assertEqual(a.handle("hello").kind, ResultKind.NO_ACTION)


class TestMetaAndPlanner(_Base):
    def test_list_routines(self) -> None:
        a = self._assistant([ToolCall("list_routines")])
        result = a.handle("what can you do")
        self.assertEqual(result.kind, ResultKind.INFO)
        names = {d["name"] for d in result.data}
        self.assertEqual(names, {"read_x", "move_x"})

    def test_get_status(self) -> None:
        a = self._assistant([ToolCall("get_status")])
        result = a.handle("are you busy")
        self.assertEqual(result.kind, ResultKind.INFO)
        self.assertEqual(result.data, {"running": False, "active": None})

    def test_planner_returns_code_proposal_not_run(self) -> None:
        code = "@routine('foo')\ndef foo(lab):\n    pass"
        a = self._assistant([ToolCall("propose_new_routine",
                                      {"name": "foo", "goal": "x", "code": code})])
        result = a.handle("write me a routine")
        self.assertEqual(result.kind, ResultKind.CODE_PROPOSAL)
        self.assertEqual(result.code_proposal.name, "foo")
        self.assertEqual(self.runner.launches, [])  # never executed
        self.assertEqual(len(self.events[CodeProposed]), 1)


class TestConfirmAndBusy(_Base):
    def test_confirm_unknown_proposal(self) -> None:
        a = self._assistant([])
        self.assertEqual(a.confirm("nope").kind, ResultKind.ERROR)

    def test_dry_run_describes_without_launching(self) -> None:
        a = self._assistant([ToolCall("move_x", {"x": 3})])
        proposal = a.handle("move").proposal
        dry = a.dry_run(proposal.id)
        self.assertEqual(dry.kind, ResultKind.INFO)
        self.assertIn("move_x", dry.message)
        self.assertEqual(self.runner.launches, [])

    def test_busy_runner_reports_busy(self) -> None:
        busy = FakeRunner(busy=True)
        a = self._assistant([ToolCall("read_x")], runner=busy)  # safe -> tries to auto-run
        result = a.handle("read")
        self.assertEqual(result.kind, ResultKind.BUSY)


if __name__ == "__main__":
    unittest.main()
