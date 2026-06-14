"""T2 planner accept flow — turn a reviewed CodeProposal into a registered routine, safely.

This is the ONLY place generated code enters the repo, and only on an explicit human action.
Guards, in order: name must be a fresh identifier; the source must parse; an AST safety scan
rejects dangerous imports/calls; the file must not already exist; then we write it, run the
verify command (default `scripts/check.py`), and **roll back the file if verify fails**. On
success the module is imported so its `@routine` self-registers.
"""
from __future__ import annotations

import ast
import importlib
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from app_apps.assistant.models import CodeProposal
from app_apps.routines.linear.registry import routine_names

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]  # .../App_Apps
_SCRIPTS_PKG = "app_apps.routines.linear.scripts"

# Generated routines should only touch lab.* + the registry import. Block obvious escapes.
_FORBIDDEN_IMPORTS = {
    "os", "sys", "subprocess", "shutil", "socket", "ctypes", "importlib", "pathlib",
    "pickle", "marshal", "requests", "urllib",
}
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "open", "__import__", "input", "globals", "locals"}


@dataclass(frozen=True)
class AcceptResult:
    accepted: bool
    message: str
    path: Optional[str] = None
    check_output: str = ""


def _scripts_dir() -> Path:
    pkg = importlib.import_module(_SCRIPTS_PKG)
    return Path(pkg.__file__).resolve().parent  # type: ignore[arg-type]


def _scan(code: str) -> list[str]:
    """Return a list of safety/structure problems with the proposed source ([] = ok)."""
    problems: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]

    has_routine = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_IMPORTS:
                    problems.append(f"forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _FORBIDDEN_IMPORTS:
                problems.append(f"forbidden import: {node.module}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _FORBIDDEN_CALLS:
                problems.append(f"forbidden call: {node.func.id}()")
        elif isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if (isinstance(target, ast.Name) and target.id == "routine") or (
                    isinstance(target, ast.Attribute) and target.attr == "routine"
                ):
                    has_routine = True

    if not has_routine:
        problems.append("no @routine-decorated function found")
    return problems


def _default_verify() -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "scripts/check.py"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stdout + result.stderr


def accept_routine(
    proposal: CodeProposal,
    *,
    scripts_dir: Optional[Path] = None,
    verify: Optional[Callable[[], tuple[bool, str]]] = None,
    register: bool = True,
) -> AcceptResult:
    """Write, verify, and register a reviewed CodeProposal. Human-triggered only."""
    name = proposal.name
    if not name.isidentifier() or name.startswith("_"):
        return AcceptResult(False, f"invalid routine name {name!r}")
    if name in routine_names():
        return AcceptResult(False, f"a routine named {name!r} already exists")

    problems = _scan(proposal.code)
    if problems:
        return AcceptResult(False, "rejected: " + "; ".join(problems))

    target_dir = scripts_dir or _scripts_dir()
    path = target_dir / f"{name}.py"
    if path.exists():
        return AcceptResult(False, f"file already exists: {path.name}")

    path.write_text(proposal.code, encoding="utf-8")

    ok, output = (verify or _default_verify)()
    if not ok:
        path.unlink(missing_ok=True)  # roll back — never leave a failing file behind
        return AcceptResult(False, "verification failed; file rolled back", check_output=output)

    if register:
        try:
            importlib.import_module(f"{_SCRIPTS_PKG}.{name}")
        except Exception as exc:
            path.unlink(missing_ok=True)
            return AcceptResult(False, f"import failed after verify: {exc!r}")

    return AcceptResult(True, f"routine {name!r} accepted and registered", path=str(path))
