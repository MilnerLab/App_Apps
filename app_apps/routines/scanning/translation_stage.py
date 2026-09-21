"""``TranslationStage`` — one linear stage, driven synchronously from a routine thread.

A handle is asynchronous: ``move_to`` returns as soon as the request is posted. That is
right for a UI, which must not block, and useless for a routine, which is a sequence —
the acquisition at point *n* must not begin until the stage has actually arrived at point
*n*. This wraps a handle so that a move is a statement rather than a request.

Three things are folded into one place, because each was previously repeated per call
site and any one of them being forgotten is a way to damage hardware or record a lie:

1. **A limit check**, against the stage's own :class:`StageSpec` from the Devices repo —
   not a table maintained alongside the scan.
2. **A blocking, reply-correlated wait.** The workers reply only after the driver's
   wait-for-motion returns, so the reply *is* motion-complete, and it is matched to this
   request by its id rather than guessed at from a bus event.
3. **The settle dwell**, because motion-done may or may not include settling and the
   cheap way to stop caring is to wait explicitly.

**Thread contract.** Call ``move_to`` on the routine's own ``TaskRunner`` thread. Replies
arrive on the IPC reader thread, so the wait cannot deadlock. Never call it from an
EventBus handler, which runs on the publisher's thread.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from base_core.ipc.blocking import blocking_request
from control_readout.base.stage_spec import StageSpec

from app_apps.io.control_readout.motorized_stage_handle import MotorizedStageHandle

log = logging.getLogger(__name__)


class TranslationStage:
    """A stage bound to the role it plays, moved one commanded position at a time."""

    def __init__(
        self,
        handle: MotorizedStageHandle,
        *,
        role: str,
        timeout_s: float,
        settle_s: float = 0.0,
        before_move: Callable[[], None] | None = None,
    ) -> None:
        """
        ``role`` names this stage in error messages and logs — "probe", "delay" — so they
        read in the experiment's vocabulary rather than the model number's.

        ``before_move`` fires immediately before every commanded move, and this is the
        single choke point through which all of them pass. That is what it is for: a
        caller that must suspend something while *anything* is in motion (a data stream
        that can only be attributed to a stationary rig, say) cannot reliably hook every
        call site, but it can hook here. Re-enabling is deliberately **not** symmetric —
        only the caller knows when the last move of a group has finished, so only the
        caller can decide when it is safe again.
        """
        self._handle = handle
        self._role = role
        self._timeout_s = timeout_s
        self._settle_s = settle_s
        self._before_move = before_move

    # -- what this stage is -----------------------------------------------

    @property
    def role(self) -> str:
        return self._role

    @property
    def spec(self) -> StageSpec:
        return self._handle.SPEC

    @property
    def limits(self) -> tuple[float, float]:
        return self.spec.limits

    @property
    def units(self) -> str:
        return self.spec.units

    @property
    def handle(self) -> MotorizedStageHandle:
        return self._handle

    # -- motion -----------------------------------------------------------

    def validate(self, position: float) -> None:
        """Raise ``StageLimitError`` unless ``position`` is inside the soft limits."""
        self.spec.validate(position, label=self._role)

    def move_to(self, position: float) -> None:
        """Command an absolute move and block until the stage is there and settled."""
        if self._before_move is not None:
            self._before_move()

        # Belt and braces: a scan validates its whole position list before it starts, so
        # reaching here with an illegal position means a bug, not a bad configuration.
        self.validate(position)

        blocking_request(
            lambda ok, err: self._handle.move_to(position, on_done=ok, on_error=err),
            timeout_s=self._timeout_s,
            what=f"{self._role} move to {position:.4f} {self.units}",
        )

        if self._settle_s > 0:
            threading.Event().wait(self._settle_s)

    def __repr__(self) -> str:
        lo, hi = self.limits
        return (f"TranslationStage(role={self._role!r}, stage={self.spec.model!r}, "
                f"limits=[{lo}, {hi}] {self.units})")
