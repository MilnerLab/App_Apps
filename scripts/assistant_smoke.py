#!/usr/bin/env python3
"""Manual live smoke test for the LLM assistant — NOT a unit test.

Exercises the real Claude client end-to-end: a natural-language command -> tool-use over the
registered routines -> validation -> safety gate. The lab is **device-less**, so motion
routines won't actually move anything; this checks the LLM mapping + gating, not hardware.

Requires:  pip install anthropic   and   ANTHROPIC_API_KEY set.
Usage:
    .venv312/Scripts/python.exe scripts/assistant_smoke.py "scan the probe from 0 to 5 mm in 0.05 steps"
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_apps.assistant.assistant import Assistant
from app_apps.assistant.client import ClaudeClient
from app_apps.assistant.models import ResultKind
from app_apps.assistant.prompt import build_system_prompt
from app_apps.routines.linear.lab import Lab
from app_apps.routines.linear.runner import LinearRoutineRunner
from base_core.framework.concurrency.task_runner import TaskRunner
from base_core.framework.events.event_bus import EventBus
import app_apps.routines.linear.scripts  # noqa: F401  registers the example routines


def main(argv: list[str]) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set — aborting (live test).")
        return 2

    command = " ".join(argv[1:]).strip() or input("command> ").strip()
    if not command:
        print("no command given.")
        return 2

    bus = EventBus()
    io = TaskRunner(ThreadPoolExecutor(max_workers=1, thread_name_prefix="smoke"))
    runner = LinearRoutineRunner(
        bus, io, lab_factory=lambda cancel, params: Lab(bus=bus, cancel=cancel, params=params)
    )
    assistant = Assistant(
        bus=bus, runner=runner, client=ClaudeClient(),
        system_prompt=build_system_prompt(), enabled=True,
    )

    result = assistant.handle(command)
    print(f"\nkind:    {result.kind.value}")
    print(f"message: {result.message}")
    if result.proposal is not None:
        p = result.proposal
        print(f"routine: {p.routine}  params: {p.params}  safe: {p.safe}")
        if result.kind is ResultKind.PROPOSAL:
            print(f"(unsafe — would require: assistant.confirm({p.id!r}))")
    if result.code_proposal is not None:
        print(f"proposed routine {result.code_proposal.name!r}:\n{result.code_proposal.code}")
    if result.data is not None:
        print(f"data:    {result.data}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
