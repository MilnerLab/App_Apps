"""Static / structural tests for the linear routine layer.

These assert *properties of the source* (no device behavior), so they catch whole classes
of regressions cheaply:
  - every module imports cleanly (no syntax / circular-import breakage);
  - `__all__` exports all resolve;
  - the cancellable-sleep invariant: no raw `time.sleep` anywhere except inside
    `bridge.cancellable_sleep`;
  - every `await_event`/`await_reply` call site in the facade passes `timeout=` AND `cancel=`
    (no unbounded, un-cancellable waits);
  - the facade only ever `publish`es Ack events — never an event type it also awaits
    (guards the self-deadlock invariant by construction);
  - the documented `lab` verb surface exists with the expected methods;
  - (gate) mypy type-checks the package, skipped if mypy isn't installed.
"""
import ast
import importlib
import inspect
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LINEAR_DIR = os.path.join(APP_ROOT, "app_apps", "routines", "linear")
MODULES = ["cancel", "config", "bridge", "registry", "lab"]


def _parse(filename: str) -> ast.AST:
    with open(os.path.join(LINEAR_DIR, filename), encoding="utf-8") as f:
        return ast.parse(f.read(), filename=filename)


def _calls(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _is_time_sleep(call: ast.Call) -> bool:
    f = call.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr == "sleep"
        and isinstance(f.value, ast.Name)
        and f.value.id == "time"
    )


class TestImportSanity(unittest.TestCase):
    def test_all_modules_import(self) -> None:
        for mod in MODULES:
            importlib.import_module(f"app_apps.routines.linear.{mod}")

    def test_package_all_exports_resolve(self) -> None:
        pkg = importlib.import_module("app_apps.routines.linear")
        for name in pkg.__all__:
            self.assertTrue(hasattr(pkg, name), f"__all__ exports missing {name!r}")


class TestSleepInvariant(unittest.TestCase):
    """No raw time.sleep except inside bridge.cancellable_sleep."""

    def test_no_raw_time_sleep_outside_cancellable_sleep(self) -> None:
        for mod in MODULES:
            tree = _parse(f"{mod}.py")
            all_sleeps = [c for c in _calls(tree) if _is_time_sleep(c)]

            allowed: list[ast.Call] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "cancellable_sleep":
                    allowed = [c for c in _calls(node) if _is_time_sleep(c)]

            disallowed = [c for c in all_sleeps if c not in allowed]
            self.assertEqual(
                disallowed,
                [],
                f"{mod}.py uses raw time.sleep outside cancellable_sleep "
                f"(line {[c.lineno for c in disallowed]})",
            )


class TestFacadeWaitInvariants(unittest.TestCase):
    """Static guarantees about how lab.py uses the bridge."""

    def setUp(self) -> None:
        self.tree = _parse("lab.py")

    def test_every_await_call_has_timeout_and_cancel(self) -> None:
        for call in _calls(self.tree):
            if isinstance(call.func, ast.Name) and call.func.id in (
                "await_event",
                "await_reply",
            ):
                kwargs = {k.arg for k in call.keywords}
                self.assertIn(
                    "timeout", kwargs, f"{call.func.id} @line {call.lineno} lacks timeout="
                )
                self.assertIn(
                    "cancel", kwargs, f"{call.func.id} @line {call.lineno} lacks cancel="
                )

    def test_facade_only_publishes_ack_events(self) -> None:
        published: set[str] = set()
        for call in _calls(self.tree):
            f = call.func
            if isinstance(f, ast.Attribute) and f.attr == "publish" and call.args:
                arg = call.args[0]
                if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
                    published.add(arg.func.id)
        self.assertTrue(
            published <= {"TraceAck", "SpectrumAck"},
            f"facade publishes non-Ack event types {published} — risks awaiting what it emits",
        )


class TestVerbSurfaceContract(unittest.TestCase):
    """The documented `lab` vocabulary exists (guards API drift for human/LLM authors)."""

    def test_subfacade_methods(self) -> None:
        from app_apps.routines.linear import lab as M

        expected = {
            M.StageFacade: ["move_to", "move_by", "position", "close"],
            M.RotatorFacade: ["rotate_to", "home"],
            M.PicomotorFacade: ["step"],
            M.ShutterFacade: ["open", "close"],
            M.ScopeFacade: ["capture", "xcorr_point"],
            M.SpectrometerFacade: ["read"],
        }
        for cls, methods in expected.items():
            for m in methods:
                self.assertTrue(hasattr(cls, m), f"{cls.__name__} missing {m!r}")

    def test_stage_move_to_signature(self) -> None:
        from app_apps.routines.linear.lab import StageFacade

        params = inspect.signature(StageFacade.move_to).parameters
        self.assertIn("position", params)

    def test_lab_surface_present_on_bare_instance(self) -> None:
        from app_apps.routines.linear.cancel import CancelToken
        from app_apps.routines.linear.lab import Lab
        from base_core.framework.events.event_bus import EventBus

        lab = Lab(bus=EventBus(), cancel=CancelToken())
        try:
            for attr in (
                "probe", "delay", "truncation", "hwp", "qwp",
                "picomotor", "shutter", "scope", "spectrometer",
            ):
                self.assertTrue(hasattr(lab, attr), f"lab missing .{attr}")
            for meth in (
                "fit_spectrum", "xcorr_point", "sleep", "checkpoint", "frange",
                "log", "record", "save", "plot", "close",
            ):
                self.assertTrue(callable(getattr(lab, meth)), f"lab.{meth} not callable")
        finally:
            lab.close()


def _mypy_available() -> bool:
    try:
        import mypy  # noqa: F401
        return True
    except ImportError:
        return False


class TestMypyGate(unittest.TestCase):
    @unittest.skipUnless(_mypy_available(), "mypy not installed")
    def test_linear_package_typechecks(self) -> None:
        result = subprocess.run(
            [
                sys.executable, "-m", "mypy",
                "-p", "app_apps.routines.linear",
                "--ignore-missing-imports",
                "--follow-imports=silent",
                "--disallow-incomplete-defs",
            ],
            cwd=APP_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0, f"mypy failed:\n{result.stdout}\n{result.stderr}"
        )


if __name__ == "__main__":
    unittest.main()
