"""The Assistant: maps a natural-language command to a routine action, safely.

Flow (`handle`): off-by-default kill switch -> ask the LLM client which tool to call (closed
set built from the registry) -> meta-tools answered directly -> routine calls validated against
the spec (one self-correction retry) -> `safe` routines auto-run, others become a `ProposalReady`
awaiting `confirm()`. The planner tool yields a `CodeProposal` that is never executed here.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from app_apps.assistant.client import LLMClient, ToolCall
from app_apps.assistant.events import (
    AssistantDisabled,
    AssistantEnabled,
    AssistantError,
    CodeProposed,
    CommandReceived,
    ProposalReady,
    RoutineAutoLaunched,
)
from app_apps.assistant.models import (
    AssistantResult,
    CodeProposal,
    Proposal,
    ResultKind,
)
from app_apps.assistant.planner import AcceptResult, accept_routine
from app_apps.assistant.tools import (
    GET_STATUS,
    LIST_ROUTINES,
    PROPOSE_NEW_ROUTINE,
    build_tools,
)
from app_apps.assistant.validation import ParamValidationError, validate_params
from app_apps.routines.linear.registry import RoutineNotFound, RoutineSpec, all_routines, get_routine
from app_apps.routines.linear.runner import LinearRoutineRunner, RoutineBusy
from base_core.framework.events.event_bus import EventBus

log = logging.getLogger(__name__)


def _format_params(params: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in params.items())


class Assistant:
    """Orchestrates LLM command -> validated routine action, with the safety gate."""

    def __init__(
        self,
        *,
        bus: EventBus,
        runner: LinearRoutineRunner,
        client: LLMClient,
        system_prompt: str = "",
        include_planner: bool = True,
        enabled: bool = False,
    ) -> None:
        self._bus = bus
        self._runner = runner
        self._client = client
        self._system = system_prompt
        self._include_planner = include_planner
        self._enabled = enabled
        self._pending: dict[str, Proposal] = {}

    # -- kill switch --------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        if not self._enabled:
            self._enabled = True
            self._bus.publish(AssistantEnabled())

    def disable(self) -> None:
        if self._enabled:
            self._enabled = False
            self._bus.publish(AssistantDisabled())

    # -- main entry ---------------------------------------------------------------------

    def handle(self, command: str) -> AssistantResult:
        """Interpret a natural-language command. Makes no LLM call while disabled."""
        if not self._enabled:
            return AssistantResult(ResultKind.DISABLED, "assistant is off")

        self._bus.publish(CommandReceived(command=command))
        tools = build_tools(all_routines(), include_planner=self._include_planner)
        call = self._client.propose(command, tools, self._system)
        if call is None:
            return AssistantResult(ResultKind.NO_ACTION, "no action proposed")

        if call.name == LIST_ROUTINES:
            return self._list_routines()
        if call.name == GET_STATUS:
            return self._status()
        if call.name == PROPOSE_NEW_ROUTINE:
            return self._code_proposal(call)
        return self._handle_routine_call(command, call, tools)

    def confirm(self, proposal_id: str) -> AssistantResult:
        """Launch a pending (unsafe) proposal after human confirmation."""
        proposal = self._pending.pop(proposal_id, None)
        if proposal is None:
            return AssistantResult(ResultKind.ERROR, f"no pending proposal {proposal_id!r}")
        return self._launch(proposal, auto=False)

    def dry_run(self, proposal_id: str) -> AssistantResult:
        """Describe what a pending proposal would do, without launching it."""
        proposal = self._pending.get(proposal_id)
        if proposal is None:
            return AssistantResult(ResultKind.ERROR, f"no pending proposal {proposal_id!r}")
        return AssistantResult(
            ResultKind.INFO,
            f"would run {proposal.routine}({_format_params(proposal.params)})",
            proposal=proposal,
        )

    def cancel(self) -> None:
        """Stop the running routine (kill switch for an in-flight action)."""
        self._runner.stop()

    def accept(self, code_proposal: CodeProposal) -> AcceptResult:
        """Human-approved: write/verify/register a planner CodeProposal. Never automatic."""
        return accept_routine(code_proposal)

    # -- internals ----------------------------------------------------------------------

    def _handle_routine_call(
        self, command: str, call: ToolCall, tools: list[dict[str, Any]]
    ) -> AssistantResult:
        try:
            spec = get_routine(call.name)
        except RoutineNotFound:
            return self._error(command, [f"unknown routine {call.name!r}"])

        try:
            params = validate_params(spec, call.arguments)
        except ParamValidationError as first:
            retry = self._client.propose(
                command, tools, self._system, feedback="; ".join(first.errors)
            )
            if retry is None or retry.name != spec.name:
                return self._error(command, first.errors)
            try:
                params = validate_params(spec, retry.arguments)
            except ParamValidationError as second:
                return self._error(command, second.errors)

        return self._propose_or_launch(spec, params)

    def _propose_or_launch(self, spec: RoutineSpec, params: dict[str, Any]) -> AssistantResult:
        proposal = Proposal(
            id=uuid.uuid4().hex[:8],
            routine=spec.name,
            params=params,
            summary=spec.summary,
            safe=spec.safe,
        )
        if spec.safe:
            return self._launch(proposal, auto=True)
        self._pending[proposal.id] = proposal
        self._bus.publish(
            ProposalReady(
                proposal_id=proposal.id,
                routine=proposal.routine,
                params=proposal.params,
                summary=proposal.summary,
            )
        )
        return AssistantResult(
            ResultKind.PROPOSAL,
            f"'{spec.name}' moves hardware — confirm to run.",
            proposal=proposal,
        )

    def _launch(self, proposal: Proposal, *, auto: bool) -> AssistantResult:
        try:
            self._runner.launch(proposal.routine, **proposal.params)
        except RoutineBusy:
            if not auto:  # keep the proposal so the user can retry after the current run
                self._pending[proposal.id] = proposal
            return AssistantResult(
                ResultKind.BUSY, "a routine is already running", proposal=proposal
            )
        if auto:
            self._bus.publish(
                RoutineAutoLaunched(routine=proposal.routine, params=proposal.params)
            )
        return AssistantResult(
            ResultKind.LAUNCHED, f"running {proposal.routine}", proposal=proposal
        )

    def _list_routines(self) -> AssistantResult:
        data = [
            {
                "name": s.name,
                "summary": s.summary,
                "safe": s.safe,
                "params": [p.name for p in s.params],
            }
            for s in all_routines().values()
        ]
        return AssistantResult(ResultKind.INFO, f"{len(data)} routines available", data=data)

    def _status(self) -> AssistantResult:
        running = self._runner.is_running
        active = self._runner.active_routine
        return AssistantResult(
            ResultKind.INFO,
            f"running: {active}" if running else "idle",
            data={"running": running, "active": active},
        )

    def _code_proposal(self, call: ToolCall) -> AssistantResult:
        if not self._include_planner:
            return AssistantResult(ResultKind.ERROR, "planner is disabled")
        cp = CodeProposal(
            name=str(call.arguments.get("name", "")),
            goal=str(call.arguments.get("goal", "")),
            code=str(call.arguments.get("code", "")),
        )
        self._bus.publish(CodeProposed(name=cp.name, goal=cp.goal))
        return AssistantResult(
            ResultKind.CODE_PROPOSAL,
            f"proposed new routine {cp.name!r} for review (not run)",
            code_proposal=cp,
        )

    def _error(self, command: str, errors: list[str]) -> AssistantResult:
        self._bus.publish(AssistantError(command=command, errors=list(errors)))
        return AssistantResult(ResultKind.ERROR, "; ".join(errors))
