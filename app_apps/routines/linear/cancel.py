"""Cooperative cancellation primitives for linear routines.

A linear routine runs on its own background thread and cannot be force-killed safely.
Instead, every blocking primitive in `bridge.py` checks a `CancelToken` at bounded
intervals and raises `RoutineCancelled` when it is set. The runner sets the token in its
`stop()`; cancellation latency is one poll tick (~50 ms) even mid-move.
"""
from __future__ import annotations

import threading


class RoutineError(Exception):
    """Base class for errors raised by the linear routine bridge."""


class RoutineCancelled(RoutineError):
    """Raised inside a blocking primitive when the routine's cancel token is set."""


class RoutineTimeout(RoutineError):
    """Raised when a blocking primitive's timeout elapses before completion."""


class CancelToken:
    """A one-way cancellation flag, safe to share across threads.

    Wraps a `threading.Event`. The routine thread reads it (via `is_cancelled` /
    `raise_if_cancelled`); the controlling thread (the runner's `stop()`) sets it via
    `cancel()`. `event` is exposed so a waiter can block on it directly.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation. Idempotent."""
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise RoutineCancelled()

    def reset(self) -> None:
        """Clear the flag so the token can be reused for a fresh run."""
        self._event.clear()

    @property
    def event(self) -> threading.Event:
        return self._event
