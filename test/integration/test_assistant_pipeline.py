"""E2E: a natural-language command drives real hardware (the plant) through the whole assistant
chain — fake LLM -> Assistant (validate + safety gate) -> confirm -> LinearRoutineRunner ->
the real lock_phase routine -> the plant converges. No network.
"""
import os
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))

import app_apps.routines.linear.scripts  # noqa: F401 — registers @routine functions (lock_phase)
from app_apps.assistant.assistant import Assistant
from app_apps.assistant.client import ToolCall
from app_apps.assistant.models import ResultKind
from app_apps.routines.linear.events import RoutineFailed
from app_apps.routines.linear.runner import LinearRoutineRunner
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
from optical_plant import OpticalPlant, make_lab_factory


class FakeLLMClient:
    """Returns a canned tool call; records how many times it was asked (to assert no call when off)."""

    def __init__(self, call: Optional[ToolCall]) -> None:
        self._call = call
        self.calls = 0

    def propose(self, command, tools, system, *, feedback=None) -> Optional[ToolCall]:
        self.calls += 1
        return self._call


class TestAssistantPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.plant = OpticalPlant(self.bus, phase_off=0.05, phase_gain=1.0)  # tau_ps=0.1 default
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.io = TaskRunner(self.executor)
        self.runner = LinearRoutineRunner(self.bus, self.io, make_lab_factory(self.plant))
        self.plant.start()
        self.failures: list[str] = []
        self.bus.subscribe(RoutineFailed, lambda e: self.failures.append(e.error))
        self.addCleanup(self._teardown)

    def _teardown(self) -> None:
        self.plant.close()
        self.executor.shutdown(wait=True)

    def _wait_idle(self, timeout: float = 20.0) -> None:
        deadline = time.time() + timeout
        while self.runner.is_running and time.time() < deadline:
            time.sleep(0.05)

    def test_nl_command_drives_plant_via_confirm(self) -> None:
        call = ToolCall(
            name="lock_phase",
            arguments={"target_rad": 0.8, "kp": 0.5, "tolerance_rad": 0.02,
                       "max_iterations": 80, "dt_s": 0.05},
        )
        assistant = Assistant(bus=self.bus, runner=self.runner, client=FakeLLMClient(call),
                              enabled=True)

        result = assistant.handle("lock the phase to 0.8 rad")
        # safety gate: lock_phase moves hardware (safe=False) -> proposal, NOT auto-run
        self.assertEqual(result.kind, ResultKind.PROPOSAL)
        self.assertFalse(self.runner.is_running)

        confirmed = assistant.confirm(result.proposal.id)
        self.assertEqual(confirmed.kind, ResultKind.LAUNCHED)
        self._wait_idle()

        self.assertEqual(self.failures, [], f"routine failed: {self.failures}")
        self.assertFalse(self.runner.is_running)
        self.assertAlmostEqual(self.plant.state().phase0, 0.8, delta=0.05)

    def test_disabled_assistant_makes_no_call(self) -> None:
        client = FakeLLMClient(None)
        assistant = Assistant(bus=self.bus, runner=self.runner, client=client, enabled=False)
        result = assistant.handle("lock the phase to 0.8 rad")
        self.assertEqual(result.kind, ResultKind.DISABLED)
        self.assertEqual(client.calls, 0)  # no LLM call while disabled
        self.assertFalse(self.runner.is_running)


if __name__ == "__main__":
    unittest.main()
