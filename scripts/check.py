#!/usr/bin/env python3
"""Single verification command for App_Apps: type-check + full unit-test suite.

Exits non-zero if anything fails, so it works as a CI gate, a pre-commit check, or a
harness ``VERIFY_CMD``. Type-checking is best-effort (skipped with a notice if mypy isn't
installed); the unit suite is the hard gate.

Run with the project interpreter so it uses the right environment and finds mypy:

    .venv312/Scripts/python.exe scripts/check.py          # Windows (this project)
    python scripts/check.py                                # if your venv is active

Scope notes:
  * mypy is scoped to app_apps.routines.linear (the type-clean package); broaden later.
  * tests run via unittest discovery over test/unit/test_*.py.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MYPY_TARGET = "app_apps.routines.linear"
MYPY_FLAGS = [
    "--ignore-missing-imports",
    "--follow-imports=silent",
    "--disallow-incomplete-defs",
]


def _run(label: str, cmd: list[str]) -> int:
    print(f"\n=== {label} ===\n$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode


def _mypy_installed() -> bool:
    try:
        import mypy  # noqa: F401

        return True
    except ImportError:
        return False


def main() -> int:
    failures: list[str] = []

    if _mypy_installed():
        rc = _run(
            "type check (mypy)",
            [sys.executable, "-m", "mypy", "-p", MYPY_TARGET, *MYPY_FLAGS],
        )
        if rc != 0:
            failures.append("mypy")
    else:
        print(
            "\n[skip] mypy not installed — type check skipped (pip install mypy to enable)",
            flush=True,
        )

    rc = _run(
        "unit tests",
        [sys.executable, "-m", "unittest", "discover", "-s", "test/unit", "-p", "test_*.py"],
    )
    if rc != 0:
        failures.append("unittest")

    print("\n" + "=" * 56)
    if failures:
        print(f"CHECK FAILED: {', '.join(failures)}")
        return 1
    print("CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
