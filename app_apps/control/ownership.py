"""
Per-stage ownership guard for the control loops.

Enforces the invariant **one stage ↔ at most one active owner** (D17/Q12). Because
the device workers are asynchronous, two routines could otherwise race on the same
controller. Contention policy is **reject** (try-acquire): since simultaneous
ownership is pathological by design, a second acquirer is refused loudly rather than
silently queued (no stale commands, no unbounded waits, deadlock-resistant).

This complements — does not replace — the per-axis in-flight serialization each
device service already performs.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Hashable, Iterator


class StageBusyError(RuntimeError):
    """Raised by :meth:`StageOwnership.acquire` when a stage is already owned."""

    def __init__(self, stage_id: Hashable, current_owner: Hashable) -> None:
        self.stage_id = stage_id
        self.current_owner = current_owner
        super().__init__(f"stage {stage_id!r} is already owned by {current_owner!r}")


class StageOwnership:
    """Thread-safe registry mapping a stage id to its current owner token.

    A stage id is any hashable handle (e.g. ``("esp301", 2)`` for axis 2). An owner
    token is any hashable identity (e.g. the loop/routine instance or its name).
    """

    def __init__(self) -> None:
        self._owners: dict[Hashable, Hashable] = {}
        self._lock = threading.Lock()

    def try_acquire(self, stage_id: Hashable, owner: Hashable) -> bool:
        """Attempt to take ownership. Return ``True`` on success.

        Idempotent for the *same* owner (re-acquiring a stage you already hold
        succeeds). Returns ``False`` if a *different* owner holds it.
        """
        with self._lock:
            current = self._owners.get(stage_id)
            if current is None or current == owner:
                self._owners[stage_id] = owner
                return True
            return False

    def acquire(self, stage_id: Hashable, owner: Hashable) -> None:
        """Like :meth:`try_acquire` but raise :class:`StageBusyError` on contention."""
        if not self.try_acquire(stage_id, owner):
            raise StageBusyError(stage_id, self._owners.get(stage_id))

    def release(self, stage_id: Hashable, owner: Hashable) -> None:
        """Release a stage. No-op unless ``owner`` currently holds it.

        Releasing a stage you don't own is ignored (it must not steal another
        owner's lock).
        """
        with self._lock:
            if self._owners.get(stage_id) == owner:
                del self._owners[stage_id]

    def owner_of(self, stage_id: Hashable) -> Hashable | None:
        with self._lock:
            return self._owners.get(stage_id)

    def is_owned(self, stage_id: Hashable) -> bool:
        with self._lock:
            return stage_id in self._owners

    @contextmanager
    def hold(self, stage_id: Hashable, owner: Hashable) -> Iterator[None]:
        """Context manager: acquire on enter (raising if busy), release on exit."""
        self.acquire(stage_id, owner)
        try:
            yield
        finally:
            self.release(stage_id, owner)
