"""The async->sync bridge: turn the framework's async event/reply model into blocking calls.

A linear routine runs on its own background thread. These primitives let it *block* on a
device action and resume when the result arrives — without deadlocking — because device
replies and telemetry events are delivered on a *different* thread (the IPC reader thread),
never the routine's own. See `docs/routine_authoring_plan.md` §3 for the threading proof.

Each primitive:
  1. subscribes/registers for the completion signal FIRST (closes the publish race),
  2. emits the command (if any),
  3. blocks on a `threading.Event`, waking every `poll` seconds to check cancel + timeout,
  4. returns the payload, unsubscribing in `finally`.

This module is device-agnostic on purpose: it takes callables (`emit`, `match`, `request`),
so the `lab` facade (R.3) supplies the concrete handle/event wiring.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional, TypeVar

from app_apps.routines.linear.cancel import (
    CancelToken,
    RoutineCancelled,
    RoutineTimeout,
)
from base_core.framework.events.event_bus import EventBus

TEvent = TypeVar("TEvent")
TReply = TypeVar("TReply")

#: Default wake interval while blocked. Bounds cancellation/timeout latency.
POLL_INTERVAL_S = 0.05


def _block_until(
    done: threading.Event,
    *,
    timeout: Optional[float],
    cancel: Optional[CancelToken],
    poll: float,
    what: str,
) -> None:
    """Block until `done` is set, raising on cancel or timeout.

    Cancel and timeout are checked *before* each bounded wait, so a token set before we
    start (or a zero timeout) is honored immediately.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        if cancel is not None and cancel.is_cancelled():
            raise RoutineCancelled(f"cancelled while waiting for {what}")
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RoutineTimeout(f"timed out after {timeout:g}s waiting for {what}")
            slice_s = min(poll, remaining)
        else:
            slice_s = poll
        if done.wait(slice_s):
            return


def await_event(
    bus: EventBus,
    event_type: type[TEvent],
    *,
    emit: Optional[Callable[[], None]] = None,
    match: Optional[Callable[[TEvent], bool]] = None,
    source: Optional[str] = None,
    timeout: Optional[float] = None,
    cancel: Optional[CancelToken] = None,
    poll: float = POLL_INTERVAL_S,
) -> TEvent:
    """Block until an event of `event_type` (optionally satisfying `match`) is published.

    Subscribes before calling `emit` so a fast reply cannot be missed. `emit` may be None
    to simply wait for the *next* such event (e.g. a streaming producer's frame). `match`
    must be fast and must not raise. Returns the matching event.
    """
    captured: dict[str, TEvent] = {}
    done = threading.Event()

    def handler(event: TEvent) -> None:
        if match is None or match(event):
            captured["event"] = event
            done.set()

    unsubscribe = bus.subscribe(event_type, handler, source=source)
    try:
        if emit is not None:
            emit()
        _block_until(
            done, timeout=timeout, cancel=cancel, poll=poll, what=event_type.__name__
        )
        return captured["event"]
    finally:
        unsubscribe()


def await_reply(
    request: Callable[[Callable[[TReply], None]], None],
    *,
    timeout: Optional[float] = None,
    cancel: Optional[CancelToken] = None,
    poll: float = POLL_INTERVAL_S,
    what: str = "reply",
) -> TReply:
    """Block until a request/reply completes.

    `request` is a function that takes an `on_reply` callback and sends the request (e.g.
    `BaseWorkerHandle._request(msg, on_reply)`); `on_reply` is invoked once on the reader
    thread with the reply object. Returns the reply as-is (the caller distinguishes
    OK/error replies); we never inspect message types here.
    """
    captured: dict[str, TReply] = {}
    done = threading.Event()

    def on_reply(reply: TReply) -> None:
        captured["reply"] = reply
        done.set()

    request(on_reply)
    _block_until(done, timeout=timeout, cancel=cancel, poll=poll, what=what)
    return captured["reply"]


def cancellable_sleep(
    seconds: float,
    *,
    cancel: Optional[CancelToken] = None,
    poll: float = POLL_INTERVAL_S,
) -> None:
    """Sleep `seconds`, but wake every `poll` to honor cancellation. Never `time.sleep` raw."""
    deadline = time.monotonic() + seconds
    while True:
        if cancel is not None and cancel.is_cancelled():
            raise RoutineCancelled("cancelled during sleep")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(poll, remaining))
