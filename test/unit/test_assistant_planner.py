"""Unit tests for the T2 planner accept flow (L4).

The verify step is stubbed in every test (never the real scripts/check.py — that would recurse,
since check.py runs this very suite).
"""
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import app_apps.routines.linear.scripts as scripts_pkg
from app_apps.assistant.models import CodeProposal
from app_apps.assistant.planner import _scan, accept_routine
from app_apps.routines.linear.registry import clear_registry, routine, routine_names


def _valid_code(name: str) -> str:
    return (
        "from app_apps.routines.linear.registry import routine\n\n"
        f"@routine({name!r})\n"
        f"def {name}(lab, x: float = 1.0):\n"
        '    """Generated test routine."""\n'
        "    lab.record(x=x)\n"
    )


class TestScan(unittest.TestCase):
    def test_accepts_valid_routine(self) -> None:
        self.assertEqual(_scan(_valid_code("ok_routine")), [])

    def test_rejects_syntax_error(self) -> None:
        self.assertTrue(any("syntax" in p for p in _scan("def (:::")))

    def test_rejects_forbidden_import(self) -> None:
        code = "import os\n@routine('a')\ndef a(lab):\n    pass\n"
        self.assertTrue(any("forbidden import" in p for p in _scan(code)))

    def test_rejects_forbidden_call(self) -> None:
        code = "@routine('a')\ndef a(lab):\n    exec('x')\n"
        self.assertTrue(any("forbidden call" in p for p in _scan(code)))

    def test_rejects_missing_routine_decorator(self) -> None:
        self.assertTrue(any("no @routine" in p for p in _scan("def a(lab):\n    pass\n")))


class TestAcceptRoutine(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(clear_registry)

    def test_invalid_name_rejected(self) -> None:
        res = accept_routine(CodeProposal("bad name", "g", _valid_code("x")),
                             verify=lambda: (True, ""))
        self.assertFalse(res.accepted)

    def test_existing_name_rejected(self) -> None:
        @routine("already_here")
        def already_here(lab):
            pass

        res = accept_routine(CodeProposal("already_here", "g", _valid_code("already_here")),
                             verify=lambda: (True, ""))
        self.assertFalse(res.accepted)
        self.assertIn("already exists", res.message)

    def test_write_without_register_to_tempdir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            name = "tmp_written"
            res = accept_routine(
                CodeProposal(name, "g", _valid_code(name)),
                scripts_dir=Path(d), verify=lambda: (True, ""), register=False,
            )
            self.assertTrue(res.accepted)
            self.assertTrue((Path(d) / f"{name}.py").exists())

    def test_verify_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            name = "tmp_rollback"
            res = accept_routine(
                CodeProposal(name, "g", _valid_code(name)),
                scripts_dir=Path(d), verify=lambda: (False, "boom"), register=False,
            )
            self.assertFalse(res.accepted)
            self.assertFalse((Path(d) / f"{name}.py").exists())  # rolled back
            self.assertIn("boom", res.check_output)

    def test_happy_path_registers(self) -> None:
        name = f"accept_test_{uuid.uuid4().hex[:6]}"
        real_dir = Path(scripts_pkg.__file__).resolve().parent
        path = real_dir / f"{name}.py"
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        self.addCleanup(lambda: sys.modules.pop(f"{scripts_pkg.__name__}.{name}", None))

        res = accept_routine(
            CodeProposal(name, "test goal", _valid_code(name)), verify=lambda: (True, "")
        )
        self.assertTrue(res.accepted, res.message)
        self.assertIn(name, routine_names())
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
