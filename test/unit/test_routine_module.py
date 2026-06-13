"""Unit tests for LinearRoutinesModule (R.5) — DI wiring + lab_factory.

Exercises the module end-to-end with a real Container/AppContext: registration creates the
runner, the lifecycle hook is added, the factory builds a device-less Lab that runs a routine
to completion, and a device registered in the container is wired through to the Lab.
"""
import logging
import os
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app_apps.io.control_readout.esp_worker_handler import EspHandle
from app_apps.routines.linear.events import RoutineCompleted
from app_apps.routines.linear.module import LinearRoutinesModule
from app_apps.routines.linear.registry import clear_registry, routine
from app_apps.routines.linear.runner import LinearRoutineRunner
from base_core.framework.app.context import AppContext
from base_core.framework.app.enums import AppStatus
from base_core.framework.di import Container
from base_core.framework.events import EventBus
from base_core.framework.lifecycle.cleanup_collection import CleanupCollection
from control_readout.esp_301.messages import MoveComplete


def _wait(predicate, timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class FakeEspHandle:
    """Stands in for EspHandle: publishes MoveComplete so a move resolves."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self.moves: list[tuple[int, float]] = []

    def move_to(self, axis: int, position: float) -> None:
        self.moves.append((axis, position))
        self._bus.publish(MoveComplete(axis=axis, position=position))


class TestLinearRoutinesModule(unittest.TestCase):
    def setUp(self) -> None:
        clear_registry()
        self.bus = EventBus()
        self.ctx = AppContext(
            config={},
            status=AppStatus.OFFLINE,
            log=logging.getLogger("test"),
            event_bus=self.bus,
            lifecycle=CleanupCollection(),
        )
        self.c = Container()
        self.module = LinearRoutinesModule()
        self.completed: list[RoutineCompleted] = []
        self.bus.subscribe(RoutineCompleted, self.completed.append)

    def tearDown(self) -> None:
        clear_registry()

    def test_register_creates_runner_singleton(self) -> None:
        self.module.register(self.c, self.ctx)
        runner = self.c.get(LinearRoutineRunner)
        self.assertIsInstance(runner, LinearRoutineRunner)
        self.assertIs(runner, self.c.get(LinearRoutineRunner))  # same instance

    def test_on_startup_registers_stop_hook(self) -> None:
        self.module.register(self.c, self.ctx)
        self.module.on_startup(self.c, self.ctx)
        # clearing the lifecycle should invoke runner.stop() harmlessly (no active routine)
        self.ctx.lifecycle.clear()  # must not raise

    def test_factory_runs_deviceless_routine(self) -> None:
        self.module.register(self.c, self.ctx)
        runner = self.c.get(LinearRoutineRunner)

        ran = []

        @routine("deviceless")
        def deviceless(lab):
            ran.append(True)
            lab.record(ok=1)

        runner.launch("deviceless")
        self.assertTrue(_wait(lambda: self.completed))
        self.assertEqual(ran, [True])

    def test_factory_wires_registered_device(self) -> None:
        fake = FakeEspHandle(self.bus)
        self.c.register_instance(EspHandle, fake)
        self.module.register(self.c, self.ctx)
        runner = self.c.get(LinearRoutineRunner)

        @routine("move_probe")
        def move_probe(lab):
            lab.probe.move_to(2.5)  # probe = ESP axis 1

        runner.launch("move_probe")
        self.assertTrue(_wait(lambda: self.completed))
        self.assertEqual(fake.moves, [(1, 2.5)])


if __name__ == "__main__":
    unittest.main()
